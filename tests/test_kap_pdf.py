import struct

import httpx
import pytest

from halka_arz_advisor.kap.pdf import (
    DEFAULT_MAX_PDF_BYTES,
    PdfCache,
    download_pdf,
    fetch_and_read_pdf,
    load_pdf_text,
    unwrap_java_byte_array,
)
from halka_arz_advisor.probe.config import ProbeConfig

ATTACHMENT_URL = "https://www.kap.org.tr/tr/api/file/download/obj-1"


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=1, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def _java_wrap(pdf_bytes: bytes) -> bytes:
    """Build a byte-accurate Java-serialized byte[] wrapper around ``pdf_bytes``,
    matching the exact format confirmed against a real KAP response (see
    halka_arz_advisor.kap.pdf's module docstring)."""
    header = bytes.fromhex("aced000575720002 5b42acf317f8060854e00200007870".replace(" ", ""))
    return header + struct.pack(">I", len(pdf_bytes)) + pdf_bytes


# --------------------------------------------------------------------------
# unwrap_java_byte_array
# --------------------------------------------------------------------------


def test_unwrap_recovers_exact_pdf_bytes(build_pdf_bytes):
    real_pdf = build_pdf_bytes(text="hello")
    wrapped = _java_wrap(real_pdf)
    assert unwrap_java_byte_array(wrapped) == real_pdf


def test_unwrap_passes_through_already_raw_pdf(build_pdf_bytes):
    real_pdf = build_pdf_bytes(text="hello")
    assert unwrap_java_byte_array(real_pdf) == real_pdf


def test_unwrap_raises_kap_response_error_for_corrupt_wrapper():
    from halka_arz_advisor.kap.exceptions import KapResponseError

    magic_but_truncated = bytes.fromhex("aced0005") + b"\x00" * 5  # magic present, no end marker
    with pytest.raises(KapResponseError):
        unwrap_java_byte_array(magic_but_truncated)


# --------------------------------------------------------------------------
# load_pdf_text: ok / scanned / empty / malformed
# --------------------------------------------------------------------------


def test_load_pdf_text_ok_for_real_text(build_pdf_bytes):
    doc = load_pdf_text(build_pdf_bytes(text="Halka Arz Fiyati belirlenen 76,60 TL"))
    assert doc.status == "ok"
    assert doc.page_count == 1
    assert "76,60" in doc.pages[0].text
    assert doc.pages[0].number == 1


def test_load_pdf_text_scanned_for_image_only_page(build_pdf_bytes):
    doc = load_pdf_text(build_pdf_bytes(with_image=True))
    assert doc.status == "scanned"


def test_load_pdf_text_empty_for_blank_page(build_pdf_bytes):
    doc = load_pdf_text(build_pdf_bytes())
    assert doc.status == "empty"


def test_load_pdf_text_malformed_for_garbage_bytes():
    doc = load_pdf_text(b"this is not a pdf at all")
    assert doc.status == "malformed"
    assert doc.error is not None


# --------------------------------------------------------------------------
# download_pdf: java-wrapped, raw, cache, errors, size limit
# --------------------------------------------------------------------------


def test_download_pdf_unwraps_java_response(httpx_mock, build_pdf_bytes):
    real_pdf = build_pdf_bytes(text="hello")
    httpx_mock.add_response(url=ATTACHMENT_URL, content=_java_wrap(real_pdf), headers={"content-type": "application/pdf"})

    result = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config())

    assert result.status == "ok"
    assert result.content == real_pdf
    assert result.from_cache is False


def test_download_pdf_accepts_already_raw_pdf(httpx_mock, build_pdf_bytes):
    real_pdf = build_pdf_bytes(text="hello")
    httpx_mock.add_response(url=ATTACHMENT_URL, content=real_pdf)

    result = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config())
    assert result.status == "ok"
    assert result.content == real_pdf


def test_download_pdf_malformed_when_not_a_pdf_after_unwrap(httpx_mock):
    httpx_mock.add_response(url=ATTACHMENT_URL, content=b"garbage, not java-wrapped, not a pdf")

    result = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config())
    assert result.status == "malformed"


def test_download_pdf_unavailable_on_http_error(httpx_mock):
    httpx_mock.add_response(url=ATTACHMENT_URL, status_code=404, content=b"not found")

    result = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config())
    assert result.status == "unavailable"
    assert "404" in result.error


def test_download_pdf_unavailable_on_transport_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=ATTACHMENT_URL)

    result = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config())
    assert result.status == "unavailable"
    assert "boom" in result.error


def test_download_pdf_unavailable_when_oversized(httpx_mock, build_pdf_bytes):
    real_pdf = build_pdf_bytes(text="x" * 500)
    httpx_mock.add_response(url=ATTACHMENT_URL, content=real_pdf)

    result = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config(), max_bytes=100)
    assert result.status == "unavailable"
    assert "size limit" in result.error


def test_download_pdf_default_size_limit_is_generous():
    assert DEFAULT_MAX_PDF_BYTES >= 10_000_000


def test_download_pdf_uses_cache_and_skips_network(httpx_mock, build_pdf_bytes, tmp_path):
    real_pdf = build_pdf_bytes(text="hello")
    cache = PdfCache(tmp_path)
    cache.put("obj-1", real_pdf)
    # No httpx_mock response registered at all — a network call would raise.

    result = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config(), cache=cache)

    assert result.status == "ok"
    assert result.content == real_pdf
    assert result.from_cache is True


def test_download_pdf_populates_cache_on_miss(httpx_mock, build_pdf_bytes, tmp_path):
    real_pdf = build_pdf_bytes(text="hello")
    httpx_mock.add_response(url=ATTACHMENT_URL, content=_java_wrap(real_pdf))
    cache = PdfCache(tmp_path)

    assert cache.get("obj-1") is None
    download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config(), cache=cache)

    assert cache.get("obj-1") == real_pdf


def test_second_run_does_not_hit_network_again(httpx_mock, build_pdf_bytes, tmp_path):
    real_pdf = build_pdf_bytes(text="hello")
    httpx_mock.add_response(url=ATTACHMENT_URL, content=_java_wrap(real_pdf))
    cache = PdfCache(tmp_path)

    first = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config(), cache=cache)
    second = download_pdf(ATTACHMENT_URL, "obj-1", config=fast_config(), cache=cache)

    assert first.from_cache is False
    assert second.from_cache is True
    assert len(httpx_mock.get_requests()) == 1


# --------------------------------------------------------------------------
# fetch_and_read_pdf: combined download + parse
# --------------------------------------------------------------------------


def test_fetch_and_read_pdf_ok(httpx_mock, build_pdf_bytes):
    real_pdf = build_pdf_bytes(text="Fiyat Tespit Raporu belirlenen 50,00 TL")
    httpx_mock.add_response(url=ATTACHMENT_URL, content=_java_wrap(real_pdf))

    result = fetch_and_read_pdf(ATTACHMENT_URL, "obj-1", disclosure_index=123, config=fast_config())

    assert result.status == "ok"
    assert len(result.pages) == 1
    assert "50,00" in result.pages[0].text


def test_fetch_and_read_pdf_reports_unavailable_without_parsing(httpx_mock):
    # download_pdf makes a single streamed attempt per call — no blind
    # retry-on-5xx for potentially large binary downloads.
    httpx_mock.add_response(url=ATTACHMENT_URL, status_code=500)

    result = fetch_and_read_pdf(ATTACHMENT_URL, "obj-1", config=fast_config(max_retries=1))
    assert result.status == "unavailable"
    assert result.pages == ()
