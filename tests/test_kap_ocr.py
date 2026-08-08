"""Mocked tests for halka_arz_advisor.kap.ocr — never requires the real
Tesseract CLI. Real PDF rendering (pypdfium2) is exercised for real
(it has no Tesseract dependency); only the ``tesseract`` subprocess
call itself is faked, via monkeypatching ``halka_arz_advisor.kap.ocr.subprocess.run``.
"""

import subprocess
from pathlib import Path

import pytest

import halka_arz_advisor.kap.ocr as ocr_module
from halka_arz_advisor.kap.ocr import OcrCache, OcrConfig, get_tesseract_version, lookup_ocr_result, ocr_pdf, ocr_pdf_extend


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def make_fake_run(*, text="Örnek Türkçe OCR metni.", per_page_text=None, fail_page=None, timeout_page=None):
    """A stand-in for subprocess.run recognizing two call shapes:
    ``["tesseract", "--version"]`` and the real per-page recognition
    call ``["tesseract", image_path, output_base, "-l", languages]`` —
    for the latter, writes the canned text to ``<output_base>.txt``
    exactly like the real CLI would, so `_ocr_image`'s read-back works.
    """
    per_page_text = per_page_text or {}
    calls: list[list[str]] = []

    def _fake_run(args, *, capture_output=True, timeout=None, **kwargs):
        calls.append(list(args))
        if args[:2] == ["tesseract", "--version"]:
            return _FakeCompletedProcess(returncode=0, stdout=b"tesseract 5.5.1\n leptonica-1.86.0")

        image_path = Path(args[1])
        output_base = Path(args[2])
        page_marker = image_path.stem  # "page-<n>"

        if timeout_page and page_marker == timeout_page:
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)
        if fail_page and page_marker == fail_page:
            return _FakeCompletedProcess(returncode=1, stderr=b"tesseract: recognition error")

        chosen_text = per_page_text.get(page_marker, text)
        output_base.with_suffix(".txt").write_text(chosen_text, encoding="utf-8")
        return _FakeCompletedProcess(returncode=0)

    _fake_run.calls = calls
    return _fake_run


def fast_config(**overrides) -> OcrConfig:
    defaults = dict(dpi=100, max_pages=30, timeout_seconds=10, languages="tur+eng")
    defaults.update(overrides)
    return OcrConfig(**defaults)


# --------------------------------------------------------------------------
# missing Tesseract
# --------------------------------------------------------------------------


def test_missing_tesseract_reports_ocr_unavailable(monkeypatch, build_pdf_bytes):
    def _raise_not_found(args, **kwargs):
        raise FileNotFoundError("tesseract not found")

    monkeypatch.setattr(ocr_module.subprocess, "run", _raise_not_found)

    result = ocr_pdf(build_pdf_bytes(with_image=True), config=fast_config())

    assert result.status == "ocr_unavailable"
    assert result.engine_version is None
    assert any("not found" in w for w in result.warnings)
    assert get_tesseract_version() is None


def test_disabled_reports_ocr_unavailable_without_touching_subprocess(monkeypatch, build_pdf_bytes):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run should never be called when OCR is disabled")

    monkeypatch.setattr(ocr_module.subprocess, "run", _fail_if_called)

    result = ocr_pdf(build_pdf_bytes(with_image=True), config=fast_config(enabled=False))
    assert result.status == "ocr_unavailable"
    assert "disabled" in result.warnings[0]


# --------------------------------------------------------------------------
# basic success + Turkish text + engine version
# --------------------------------------------------------------------------


def test_ocr_ok_recovers_turkish_text_and_engine_version(monkeypatch, build_pdf_bytes):
    monkeypatch.setattr(
        ocr_module.subprocess, "run", make_fake_run(text="Halka arz fiyatı 76,60 TL olarak belirlenmiştir.")
    )

    result = ocr_pdf(build_pdf_bytes(with_image=True), config=fast_config())

    assert result.status == "ocr_ok"
    assert result.engine_version == "5.5.1"
    assert result.languages == "tur+eng"
    assert result.processed_page_count == 1
    assert result.total_page_count == 1
    assert len(result.pages) == 1
    assert result.pages[0].number == 1
    assert "76,60 TL" in result.pages[0].text
    assert result.warnings == ()


# --------------------------------------------------------------------------
# timeout
# --------------------------------------------------------------------------


def test_page_timeout_recorded_as_warning(monkeypatch, build_pdf_bytes):
    monkeypatch.setattr(ocr_module.subprocess, "run", make_fake_run(timeout_page="page-1"))

    result = ocr_pdf(build_pdf_bytes(with_image=True), config=fast_config(timeout_seconds=5))

    assert result.status == "ocr_failed"  # the only page timed out -> nothing recovered
    assert result.pages == ()
    assert any("timed out" in w for w in result.warnings)


# --------------------------------------------------------------------------
# failed page rendering
# --------------------------------------------------------------------------


def test_failed_page_rendering_recorded_as_warning_others_continue(monkeypatch, build_pdf_bytes):
    real_render = ocr_module._render_page_to_png
    call_count = {"n": 0}

    def _flaky_render(pdf_document, page_index, *, dpi, output_path):
        call_count["n"] += 1
        if page_index == 0:
            raise RuntimeError("simulated rendering failure")
        real_render(pdf_document, page_index, dpi=dpi, output_path=output_path)

    monkeypatch.setattr(ocr_module, "_render_page_to_png", _flaky_render)
    monkeypatch.setattr(ocr_module.subprocess, "run", make_fake_run(text="sayfa metni"))

    # Two real pages so page 0 fails to render but page 1 still gets OCR'd.
    import pypdfium2 as pdfium

    writer_bytes = build_pdf_bytes(with_image=True)
    two_page_doc = pdfium.PdfDocument(writer_bytes)
    new_doc = pdfium.PdfDocument.new()
    new_doc.import_pages(two_page_doc, pages=[0, 0])
    import io

    buf = io.BytesIO()
    new_doc.save(buf)
    two_page_pdf_bytes = buf.getvalue()

    result = ocr_pdf(two_page_pdf_bytes, config=fast_config())

    assert call_count["n"] == 2
    assert result.status == "ocr_partial"
    assert len(result.pages) == 1
    assert result.pages[0].number == 2
    assert any("failed to render" in w for w in result.warnings)


# --------------------------------------------------------------------------
# partial long-document OCR (page limit)
# --------------------------------------------------------------------------


def test_document_longer_than_max_pages_is_partial(monkeypatch, build_pdf_bytes):
    import io

    import pypdfium2 as pdfium

    one_page = pdfium.PdfDocument(build_pdf_bytes(with_image=True))
    combined = pdfium.PdfDocument.new()
    combined.import_pages(one_page, pages=[0, 0, 0, 0, 0])  # 5-page document
    buf = io.BytesIO()
    combined.save(buf)
    five_page_pdf_bytes = buf.getvalue()

    monkeypatch.setattr(ocr_module.subprocess, "run", make_fake_run(text="sayfa"))

    result = ocr_pdf(five_page_pdf_bytes, config=fast_config(max_pages=2))

    assert result.status == "ocr_partial"
    assert result.total_page_count == 5
    assert result.processed_page_count == 2
    assert len(result.pages) == 2
    assert any("only the first 2" in w for w in result.warnings)


# --------------------------------------------------------------------------
# cache hit: unchanged documents must not be OCR'd again
# --------------------------------------------------------------------------


def test_cache_hit_does_not_invoke_tesseract_again(monkeypatch, build_pdf_bytes, tmp_path):
    fake_run = make_fake_run(text="ilk calistirma metni")
    monkeypatch.setattr(ocr_module.subprocess, "run", fake_run)

    cache = OcrCache(tmp_path / "kap_ocr")
    pdf_bytes = build_pdf_bytes(with_image=True)
    config = fast_config()

    first = ocr_pdf(pdf_bytes, config=config, cache=cache)
    assert first.status == "ocr_ok"
    calls_after_first = len(fake_run.calls)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("tesseract must not be invoked again for an unchanged document")

    monkeypatch.setattr(ocr_module.subprocess, "run", _fail_if_called)

    second = ocr_pdf(pdf_bytes, config=config, cache=cache)
    assert second.status == "ocr_ok"
    assert second.pages[0].text == first.pages[0].text

    looked_up = lookup_ocr_result(pdf_bytes, config=config, cache=cache)
    assert looked_up is not None
    assert looked_up.pages[0].text == first.pages[0].text
    assert calls_after_first > 0  # sanity: the first run really did call tesseract


def test_lookup_ocr_result_is_none_on_cache_miss(build_pdf_bytes, tmp_path):
    cache = OcrCache(tmp_path / "kap_ocr")
    result = lookup_ocr_result(build_pdf_bytes(with_image=True), config=fast_config(), cache=cache)
    assert result is None


def test_different_dpi_is_a_separate_cache_entry(monkeypatch, build_pdf_bytes, tmp_path):
    monkeypatch.setattr(ocr_module.subprocess, "run", make_fake_run(text="metin"))
    cache = OcrCache(tmp_path / "kap_ocr")
    pdf_bytes = build_pdf_bytes(with_image=True)

    ocr_pdf(pdf_bytes, config=fast_config(dpi=100), cache=cache)
    assert lookup_ocr_result(pdf_bytes, config=fast_config(dpi=200), cache=cache) is None
    assert lookup_ocr_result(pdf_bytes, config=fast_config(dpi=100), cache=cache) is not None


# --------------------------------------------------------------------------
# ocr_pdf_extend: deepen an already-OCR'd document without redoing pages
# already cached (kap.allocation_ocr's scoped deep-OCR fallback)
# --------------------------------------------------------------------------


def test_ocr_pdf_extend_reuses_cached_pages_and_only_ocrs_the_new_ones(monkeypatch, build_pdf_bytes, tmp_path):
    import io

    import pypdfium2 as pdfium

    one_page = pdfium.PdfDocument(build_pdf_bytes(with_image=True))
    combined = pdfium.PdfDocument.new()
    combined.import_pages(one_page, pages=[0, 0, 0, 0, 0])  # 5-page document
    buf = io.BytesIO()
    combined.save(buf)
    five_page_pdf_bytes = buf.getvalue()

    cache = OcrCache(tmp_path / "kap_ocr")
    monkeypatch.setattr(
        ocr_module.subprocess, "run", make_fake_run(per_page_text={"page-1": "sayfa bir", "page-2": "sayfa iki"})
    )

    shallow = ocr_pdf(five_page_pdf_bytes, config=fast_config(max_pages=2), cache=cache)
    assert shallow.status == "ocr_partial"
    assert shallow.processed_page_count == 2

    def _fail_on_already_cached_pages(args, *, capture_output=True, timeout=None, **kwargs):
        if args[:2] == ["tesseract", "--version"]:
            return _FakeCompletedProcess(returncode=0, stdout=b"tesseract 5.5.1\n leptonica-1.86.0")
        image_path = Path(args[1])
        if image_path.stem in ("page-1", "page-2"):
            raise AssertionError(f"{image_path.stem} was already cached by the shallow run and must not be re-OCR'd")
        output_base = Path(args[2])
        output_base.with_suffix(".txt").write_text(f"yeni metin {image_path.stem}", encoding="utf-8")
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(ocr_module.subprocess, "run", _fail_on_already_cached_pages)

    deepened = ocr_pdf_extend(five_page_pdf_bytes, config=fast_config(), cache=cache, target_page_count=5)

    assert deepened.status == "ocr_ok"
    assert deepened.processed_page_count == 5
    assert deepened.total_page_count == 5
    assert deepened.pages[0].text == "sayfa bir"  # reused from the shallow run's cache, not re-rendered/re-OCR'd
    assert deepened.pages[1].text == "sayfa iki"
    assert deepened.pages[2].text == "yeni metin page-3"
    assert deepened.pages[4].text == "yeni metin page-5"

    # The manifest was extended (not left at the shallow processed_page_count
    # of 2), so a later plain ocr_pdf()/lookup_ocr_result() call against the
    # same document transparently benefits from the deeper scan too.
    looked_up = lookup_ocr_result(five_page_pdf_bytes, config=fast_config(), cache=cache)
    assert looked_up is not None
    assert looked_up.status == "ocr_ok"
    assert looked_up.processed_page_count == 5
