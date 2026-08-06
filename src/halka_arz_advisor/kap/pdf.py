"""Download and read PDF attachments from KAP.

**KAP's file-download endpoint does not return a raw PDF.** Confirmed
live against a real prospectus attachment
(``GET https://www.kap.org.tr/tr/api/file/download/{objId}``, which
claims ``Content-Type: application/pdf`` but is not one): the response
body is a **Java-serialized ``byte[]``** (``java.io.ObjectOutputStream``
format) wrapping the actual PDF bytes, byte-for-byte verified against a
204-page real prospectus (``AC ED 00 05`` magic, a big-endian length
prefix, then the embedded PDF starting at ``%PDF-1.4`` and ending at
``%%EOF``, with the unwrapped length matching the length prefix exactly
and consuming the entire response). This is why validating "is it a
PDF" by the HTTP ``Content-Type`` header is not reliable here — it has
to be the magic bytes, checked *after* unwrapping.

No OCR: a PDF with no extractable text layer is reported with
``status="scanned"``/``"empty"``, never silently treated as having no
content, and never guessed at.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from pypdf import PdfReader

from ..probe.config import ProbeConfig
from .attachments import DISCLOSURE_PAGE_URL_TEMPLATE
from .exceptions import KapResponseError

PdfStatus = Literal["ok", "scanned", "empty", "malformed", "unavailable"]

DEFAULT_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 60.0

# Below this average extracted characters per page, a PDF with embedded
# images is treated as scanned rather than "ok" — calibrated against
# real KAP watermark-only pages (~88 chars/page: just a "Doğrulama Kodu"
# verification stamp) vs. real body-text pages (thousands of chars/page).
_SCANNED_AVG_CHARS_PER_PAGE_THRESHOLD = 150

_JAVA_SERIALIZATION_MAGIC = b"\xac\xed\x00\x05"
_JAVA_ARRAY_END_MARKER = b"\x78\x70"


class PdfCache:
    """Disk cache for downloaded (already-unwrapped) PDF bytes, keyed by
    KAP's attachment ``objId``. KAP attachments are immutable once
    published, so cache entries never expire — repeated CLI runs never
    re-download an attachment already on disk."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, obj_id: str) -> Path:
        return self.directory / f"{obj_id}.pdf"

    def get(self, obj_id: str) -> bytes | None:
        path = self._path(obj_id)
        return path.read_bytes() if path.exists() else None

    def put(self, obj_id: str, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(obj_id).write_bytes(data)

    def has(self, obj_id: str) -> bool:
        return self._path(obj_id).exists()


def unwrap_java_byte_array(raw: bytes) -> bytes:
    """Extract the real PDF bytes out of KAP's Java-serialized ``byte[]`` wrapper.

    Format (confirmed live — see module docstring)::

        AC ED 00 05                     magic + stream version
        75                              TC_ARRAY
        72 00 02 5B 42                  TC_CLASSDESC for "[B" (byte array)
        <8 bytes>                       serialVersionUID
        02 00 00                        classDescFlags + field count
        78 70                           TC_ENDBLOCKDATA + TC_NULL
        <4 bytes, big-endian uint32>    PDF byte length
        <PDF bytes>                     the actual %PDF-... content

    If ``raw`` doesn't start with the Java serialization magic, it's
    returned unchanged — KAP may serve some attachments unwrapped, and
    guessing at a different format here would risk corrupting a response
    this function doesn't actually recognize.
    """
    if not raw.startswith(_JAVA_SERIALIZATION_MAGIC):
        return raw
    try:
        marker_index = raw.index(_JAVA_ARRAY_END_MARKER, 10)
        length = struct.unpack(">I", raw[marker_index + 2 : marker_index + 6])[0]
        start = marker_index + 6
        return raw[start : start + length]
    except (ValueError, struct.error) as exc:
        raise KapResponseError(f"could not unwrap KAP's Java-serialized attachment response: {exc}") from exc


@dataclass(frozen=True, slots=True)
class PdfDownloadResult:
    status: PdfStatus
    content: bytes | None
    error: str | None
    from_cache: bool


def download_pdf(
    attachment_url: str,
    obj_id: str,
    *,
    disclosure_index: int | None = None,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    cache: PdfCache | None = None,
    max_bytes: int = DEFAULT_MAX_PDF_BYTES,
    timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    cache_only: bool = False,
) -> PdfDownloadResult:
    """Download one attachment, unwrap it, and validate it's a real PDF.

    Checks the cache first (keyed by ``obj_id``). Enforces ``max_bytes``
    while streaming (stops reading once exceeded — the transfer isn't
    pre-announced, so this bounds total bytes read rather than
    pre-rejecting) and an explicit ``timeout_seconds`` for the download,
    separate from the shorter default read timeout used for small JSON
    API calls elsewhere in the project (attachments can be tens of MB).

    If ``cache_only`` is set, a cache miss is reported as
    ``"unavailable"`` immediately — no network request is made. Used by
    the Ollama analysis layer, which must only ever analyze documents
    already downloaded by a prior ``--parse-documents`` run.
    """
    if cache is not None:
        cached = cache.get(obj_id)
        if cached is not None:
            status: PdfStatus = "ok" if cached.startswith(b"%PDF-") else "malformed"
            return PdfDownloadResult(status=status, content=cached, error=None, from_cache=True)

    if cache_only:
        return PdfDownloadResult(
            status="unavailable",
            content=None,
            error="not in cache (cache_only mode: KAP documents are not re-downloaded)",
            from_cache=False,
        )

    cfg = config or ProbeConfig()
    owns_client = client is None
    http_client = client or httpx.Client(
        headers={"User-Agent": cfg.user_agent},
        timeout=httpx.Timeout(
            connect=cfg.connect_timeout_seconds,
            read=timeout_seconds,
            write=timeout_seconds,
            pool=cfg.connect_timeout_seconds,
        ),
    )
    headers = {}
    if disclosure_index is not None:
        headers["Referer"] = DISCLOSURE_PAGE_URL_TEMPLATE.format(index=disclosure_index)

    try:
        try:
            with http_client.stream("GET", attachment_url, headers=headers) as response:
                if response.status_code >= 400:
                    return PdfDownloadResult(
                        status="unavailable", content=None, error=f"HTTP {response.status_code}", from_cache=False
                    )
                chunks: list[bytes] = []
                total = 0
                exceeded = False
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        exceeded = True
                        break
                    chunks.append(chunk)
                if exceeded:
                    return PdfDownloadResult(
                        status="unavailable",
                        content=None,
                        error=f"attachment exceeded the {max_bytes}-byte size limit",
                        from_cache=False,
                    )
                raw = b"".join(chunks)
        except httpx.TransportError as exc:
            return PdfDownloadResult(
                status="unavailable", content=None, error=f"transport error: {exc}", from_cache=False
            )
    finally:
        if owns_client:
            http_client.close()

    try:
        pdf_bytes = unwrap_java_byte_array(raw)
    except KapResponseError as exc:
        return PdfDownloadResult(status="malformed", content=None, error=str(exc), from_cache=False)

    if not pdf_bytes.startswith(b"%PDF-"):
        return PdfDownloadResult(
            status="malformed",
            content=pdf_bytes,
            error="downloaded content does not start with %PDF- after unwrapping",
            from_cache=False,
        )

    if cache is not None:
        cache.put(obj_id, pdf_bytes)

    return PdfDownloadResult(status="ok", content=pdf_bytes, error=None, from_cache=False)


@dataclass(frozen=True, slots=True)
class PdfPage:
    number: int  # 1-indexed, matching how a human would cite "page N"
    text: str


@dataclass(frozen=True, slots=True)
class PdfDocument:
    pages: tuple[PdfPage, ...]
    status: PdfStatus
    page_count: int
    error: str | None


def _page_has_image(page) -> bool:
    resources = page.get("/Resources")
    if not resources:
        return False
    xobjects = resources.get("/XObject")
    if not xobjects:
        return False
    for ref in xobjects.values():
        try:
            obj = ref.get_object()
        except Exception:  # noqa: BLE001 - a broken xobject ref shouldn't crash detection
            continue
        if obj.get("/Subtype") == "/Image":
            return True
    return False


def load_pdf_text(pdf_bytes: bytes) -> PdfDocument:
    """Open validated PDF bytes and extract each page's text layer.

    Never raises for a malformed/scanned/empty PDF — that's reported via
    ``status`` instead, so callers always get a result to record.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf's own error types vary by failure mode
        return PdfDocument(pages=(), status="malformed", page_count=0, error=f"could not open as a PDF: {exc}")

    if page_count == 0:
        return PdfDocument(pages=(), status="empty", page_count=0, error="PDF has zero pages")

    pages: list[PdfPage] = []
    total_chars = 0
    has_image = False
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one broken page shouldn't fail the whole document
            text = ""
        pages.append(PdfPage(number=i + 1, text=text))
        total_chars += len(text.strip())
        if not has_image:
            has_image = _page_has_image(page)

    if total_chars == 0:
        status: PdfStatus = "scanned" if has_image else "empty"
        return PdfDocument(pages=tuple(pages), status=status, page_count=page_count, error=None)

    avg_chars_per_page = total_chars / page_count
    if avg_chars_per_page < _SCANNED_AVG_CHARS_PER_PAGE_THRESHOLD and has_image:
        return PdfDocument(pages=tuple(pages), status="scanned", page_count=page_count, error=None)

    return PdfDocument(pages=tuple(pages), status="ok", page_count=page_count, error=None)


@dataclass(frozen=True, slots=True)
class PdfFetchResult:
    status: PdfStatus
    pages: tuple[PdfPage, ...]
    error: str | None
    from_cache: bool


def fetch_and_read_pdf(
    attachment_url: str,
    obj_id: str,
    *,
    disclosure_index: int | None = None,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    cache: PdfCache | None = None,
    max_bytes: int = DEFAULT_MAX_PDF_BYTES,
    timeout_seconds: float = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    cache_only: bool = False,
) -> PdfFetchResult:
    """Download (or read from cache) one attachment and extract its text."""
    download = download_pdf(
        attachment_url,
        obj_id,
        disclosure_index=disclosure_index,
        config=config,
        client=client,
        cache=cache,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        cache_only=cache_only,
    )
    if download.status != "ok" or download.content is None:
        return PdfFetchResult(status=download.status, pages=(), error=download.error, from_cache=download.from_cache)

    document = load_pdf_text(download.content)
    return PdfFetchResult(
        status=document.status, pages=document.pages, error=document.error, from_cache=download.from_cache
    )
