"""OCR fallback for scanned/empty KAP PDFs.

Runs only when normal digital-text extraction (see
:mod:`halka_arz_advisor.kap.pdf`) reports ``"scanned"`` or ``"empty"``
for a document — a PDF that already has a usable text layer is never
OCR'd. Renders each page to an image via ``pypdfium2`` and recognizes
it with the local Tesseract CLI (``tur+eng``) — no paid or hosted OCR
API, and no third-party Python OCR wrapper package (matches the
project's established "plain subprocess/HTTP, no SDK" style — see
:mod:`halka_arz_advisor.gemini.client`'s docstring for the same
reasoning applied to Gemini).

Two-tier disk cache under ``data/cache/kap_ocr/``, keyed by (PDF
content hash, languages, DPI, OCR pipeline version) plus a page number
for the per-page text — an unchanged document is never OCR'd twice;
see :func:`lookup_ocr_result` (pure cache read, no Tesseract/pypdfium2
call at all) vs. :func:`ocr_pdf` (checks the cache first, then runs the
real pipeline on a miss).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium

from .pdf import PdfPage

OcrStatus = Literal["ocr_ok", "ocr_partial", "ocr_unavailable", "ocr_failed"]

DEFAULT_ENABLED = True
DEFAULT_DPI = 220
DEFAULT_MAX_PAGES = 30
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_LANGUAGES = "tur+eng"

# Bump to invalidate every cached OCR result if the rendering/recognition
# pipeline itself changes (DPI/language stay separate cache-key
# components since those are independently configurable).
OCR_VERSION = "1"

DEFAULT_OCR_CACHE_DIR = Path("data") / "cache" / "kap_ocr"


class TesseractUnavailableError(Exception):
    """The Tesseract CLI is not installed / not found on ``PATH``."""


@dataclass(frozen=True, slots=True)
class OcrConfig:
    enabled: bool = DEFAULT_ENABLED
    dpi: int = DEFAULT_DPI
    max_pages: int = DEFAULT_MAX_PAGES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    languages: str = DEFAULT_LANGUAGES


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def load_ocr_config_from_env() -> OcrConfig:
    """``OCR_ENABLED``/``OCR_DPI``/``OCR_MAX_PAGES``/``OCR_TIMEOUT_SECONDS``
    (see ``.env.example``) — languages are fixed at ``tur+eng`` per the
    brief, not independently configurable."""
    return OcrConfig(
        enabled=_bool_env("OCR_ENABLED", DEFAULT_ENABLED),
        dpi=_int_env("OCR_DPI", DEFAULT_DPI),
        max_pages=_int_env("OCR_MAX_PAGES", DEFAULT_MAX_PAGES),
        timeout_seconds=_float_env("OCR_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        languages=DEFAULT_LANGUAGES,
    )


def get_tesseract_version() -> str | None:
    """Return the installed Tesseract version (e.g. ``"5.5.1"``), or
    ``None`` if the Tesseract CLI isn't available at all — doubles as
    the availability check feeding ``"ocr_unavailable"``."""
    try:
        result = subprocess.run(["tesseract", "--version"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    first_line = result.stdout.decode("utf-8", errors="replace").splitlines()[0] if result.stdout else ""
    parts = first_line.split()
    return parts[1] if len(parts) >= 2 else (first_line or None)


@dataclass(frozen=True, slots=True)
class OcrResult:
    status: OcrStatus
    pages: tuple[PdfPage, ...]
    warnings: tuple[str, ...]
    processed_page_count: int
    total_page_count: int
    languages: str
    engine_version: str | None


class OcrCache:
    """Disk cache under ``data/cache/kap_ocr/``: a per-document manifest
    (status, page counts, warnings, engine version) plus per-page OCR
    text, both keyed by (PDF content hash, languages, DPI,
    :data:`OCR_VERSION`) — a page additionally by its page number."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _manifest_key(self, content_hash: str, languages: str, dpi: int) -> str:
        raw = f"{content_hash}|{languages}|{dpi}|{OCR_VERSION}|manifest"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _page_key(self, content_hash: str, page_number: int, languages: str, dpi: int) -> str:
        raw = f"{content_hash}|{page_number}|{languages}|{dpi}|{OCR_VERSION}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _manifest_path(self, content_hash: str, languages: str, dpi: int) -> Path:
        return self.directory / f"{self._manifest_key(content_hash, languages, dpi)}.json"

    def _page_path(self, content_hash: str, page_number: int, languages: str, dpi: int) -> Path:
        return self.directory / f"{self._page_key(content_hash, page_number, languages, dpi)}.json"

    def get_manifest(self, content_hash: str, *, languages: str, dpi: int) -> dict | None:
        path = self._manifest_path(content_hash, languages, dpi)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put_manifest(self, content_hash: str, *, languages: str, dpi: int, manifest: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._manifest_path(content_hash, languages, dpi).write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )

    def get_page_text(self, content_hash: str, page_number: int, *, languages: str, dpi: int) -> str | None:
        path = self._page_path(content_hash, page_number, languages, dpi)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["text"]

    def put_page_text(self, content_hash: str, page_number: int, *, languages: str, dpi: int, text: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._page_path(content_hash, page_number, languages, dpi).write_text(
            json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8"
        )


def lookup_ocr_result(pdf_bytes: bytes, *, config: OcrConfig, cache: OcrCache) -> OcrResult | None:
    """Pure cache read — never renders a page or invokes Tesseract.

    Returns ``None`` on a cache miss (this document, at this
    languages/DPI/version combination, has never been OCR'd), or the
    previously produced :class:`OcrResult` reconstructed entirely from
    cached data on a hit.
    """
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()
    manifest = cache.get_manifest(content_hash, languages=config.languages, dpi=config.dpi)
    if manifest is None:
        return None

    pages = []
    for page_number in range(1, manifest["processed_page_count"] + 1):
        text = cache.get_page_text(content_hash, page_number, languages=config.languages, dpi=config.dpi)
        if text is not None:
            pages.append(PdfPage(number=page_number, text=text))

    return OcrResult(
        status=manifest["status"],
        pages=tuple(pages),
        warnings=tuple(manifest["warnings"]),
        processed_page_count=manifest["processed_page_count"],
        total_page_count=manifest["total_page_count"],
        languages=manifest["languages"],
        engine_version=manifest["engine_version"],
    )


def _render_page_to_png(pdf_document: "pdfium.PdfDocument", page_index: int, *, dpi: int, output_path: Path) -> None:
    """Render one page to a PNG file. A standalone module function (not
    inlined) so tests can monkeypatch it to simulate a rendering
    failure without needing a real broken PDF."""
    page = pdf_document.get_page(page_index)
    try:
        bitmap = page.render(scale=dpi / 72)
        try:
            bitmap.to_pil().save(output_path)
        finally:
            bitmap.close()
    finally:
        page.close()


def _ocr_image(image_path: Path, output_base: Path, *, languages: str, timeout_seconds: float) -> str:
    """Run the Tesseract CLI over one rendered page image, returning its
    recognized text.

    Paths are resolved (symlinks followed) before being handed to
    Tesseract — confirmed necessary against a real local Tesseract/
    Leptonica build that fails to open an image through a symlinked
    temp-directory path (e.g. macOS's ``/tmp`` -> ``/private/tmp``)
    while the resolved path works fine.
    """
    try:
        result = subprocess.run(
            ["tesseract", str(image_path.resolve()), str(output_base.resolve()), "-l", languages],
            capture_output=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise TesseractUnavailableError("tesseract CLI not found on PATH") from exc

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"tesseract exited {result.returncode}: {stderr[:500]}")

    return output_base.with_suffix(".txt").read_text(encoding="utf-8")


def ocr_pdf(pdf_bytes: bytes, *, config: OcrConfig, cache: OcrCache | None = None) -> OcrResult:
    """OCR a scanned/empty PDF's pages, honoring ``config.max_pages`` and
    ``config.timeout_seconds`` (applied per page).

    Checks ``cache`` first via :func:`lookup_ocr_result` — an unchanged
    document (same content hash/languages/DPI/:data:`OCR_VERSION`) is
    never OCR'd twice. Never raises: every failure mode (disabled,
    Tesseract missing, a page failing to render, a page timing out) is
    reported through ``status``/``warnings`` instead.
    """
    if not config.enabled:
        return OcrResult(
            status="ocr_unavailable",
            pages=(),
            warnings=("OCR is disabled (OCR_ENABLED=false)",),
            processed_page_count=0,
            total_page_count=0,
            languages=config.languages,
            engine_version=None,
        )

    if cache is not None:
        cached_result = lookup_ocr_result(pdf_bytes, config=config, cache=cache)
        if cached_result is not None:
            return cached_result

    engine_version = get_tesseract_version()
    if engine_version is None:
        return OcrResult(
            status="ocr_unavailable",
            pages=(),
            warnings=("tesseract CLI not found on PATH",),
            processed_page_count=0,
            total_page_count=0,
            languages=config.languages,
            engine_version=None,
        )

    content_hash = hashlib.sha256(pdf_bytes).hexdigest()

    try:
        pdf_document = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - pypdfium2's own error types vary by failure mode
        return OcrResult(
            status="ocr_failed",
            pages=(),
            warnings=(f"could not open PDF for rendering: {exc}",),
            processed_page_count=0,
            total_page_count=0,
            languages=config.languages,
            engine_version=engine_version,
        )

    try:
        total_page_count = len(pdf_document)
        pages_to_process = min(total_page_count, config.max_pages)
        is_page_limited = total_page_count > config.max_pages

        pages: list[PdfPage] = []
        warnings: list[str] = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for page_index in range(pages_to_process):
                page_number = page_index + 1

                image_path = tmp_path / f"page-{page_number}.png"
                try:
                    _render_page_to_png(pdf_document, page_index, dpi=config.dpi, output_path=image_path)
                except Exception as exc:  # noqa: BLE001 - one broken page shouldn't fail the whole document
                    warnings.append(f"page {page_number}: failed to render: {exc}")
                    continue

                try:
                    text = _ocr_image(
                        image_path,
                        tmp_path / f"page-{page_number}",
                        languages=config.languages,
                        timeout_seconds=config.timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    warnings.append(f"page {page_number}: OCR timed out after {config.timeout_seconds}s")
                    continue
                except TesseractUnavailableError:
                    warnings.append(f"page {page_number}: tesseract CLI not found")
                    continue
                except Exception as exc:  # noqa: BLE001 - one broken page shouldn't fail the whole document
                    warnings.append(f"page {page_number}: OCR failed: {exc}")
                    continue

                pages.append(PdfPage(number=page_number, text=text))
                if cache is not None:
                    cache.put_page_text(
                        content_hash, page_number, languages=config.languages, dpi=config.dpi, text=text
                    )
    finally:
        pdf_document.close()

    if is_page_limited:
        warnings.append(
            f"document has {total_page_count} page(s); only the first {pages_to_process} "
            f"(OCR_MAX_PAGES={config.max_pages}) were OCR'd"
        )

    if not pages:
        status: OcrStatus = "ocr_failed"
    elif is_page_limited or len(pages) < pages_to_process:
        status = "ocr_partial"
    else:
        status = "ocr_ok"

    result = OcrResult(
        status=status,
        pages=tuple(pages),
        warnings=tuple(warnings),
        processed_page_count=pages_to_process,
        total_page_count=total_page_count,
        languages=config.languages,
        engine_version=engine_version,
    )

    if cache is not None:
        cache.put_manifest(
            content_hash,
            languages=config.languages,
            dpi=config.dpi,
            manifest={
                "status": result.status,
                "warnings": list(result.warnings),
                "processed_page_count": result.processed_page_count,
                "total_page_count": result.total_page_count,
                "languages": result.languages,
                "engine_version": result.engine_version,
            },
        )

    return result


def ocr_pdf_extend(pdf_bytes: bytes, *, config: OcrConfig, cache: OcrCache, target_page_count: int) -> OcrResult:
    """Ensure pages ``1..min(total_page_count, target_page_count)`` of
    ``pdf_bytes`` are OCR'd, going *deeper* into a document than
    :func:`ocr_pdf`'s own ``config.max_pages`` budget without redoing
    any page already OCR'd.

    :class:`OcrCache`'s per-page entries (:meth:`OcrCache.get_page_text`/
    :meth:`put_page_text`) are keyed by ``(content_hash, page_number,
    languages, dpi)`` — never by how many pages a given run asked for —
    so this reuses any page already cached by an earlier, shallower
    :func:`ocr_pdf`/:func:`ocr_pdf_extend` call and only renders+OCRs
    pages that are genuinely still missing. This is the one thing
    :func:`ocr_pdf` itself can't do: its own manifest-based
    :func:`lookup_ocr_result` short-circuit returns whatever was cached
    *first*, regardless of ``config.max_pages``, so simply calling
    :func:`ocr_pdf` again with a larger ``max_pages`` against an
    already-manifested document would silently keep returning the old,
    shallower result forever.

    The manifest for ``(content_hash, languages, dpi)`` is updated to
    the deeper ``processed_page_count`` reached here (never regressed to
    a shallower one) — so a plain :func:`ocr_pdf`/:func:`lookup_ocr_result`
    call against the same document afterward transparently benefits from
    the deeper scan too. This is a deliberate, documented trade-off: the
    *global* :data:`DEFAULT_MAX_PAGES`/``OCR_MAX_PAGES`` default this
    project uses everywhere else is never changed by calling this
    function, but a specific document this function has already deep-
    scanned once stays deep-scanned for every future consumer, for free
    — exactly the "persist the additional OCR work so repeated runs are
    cheap" property a scoped, on-demand fallback needs (see
    :mod:`halka_arz_advisor.kap.allocation_ocr`, the one caller of this
    function today).

    Never raises, same failure-reporting convention as :func:`ocr_pdf`.
    """
    if not config.enabled:
        return OcrResult(
            status="ocr_unavailable",
            pages=(),
            warnings=("OCR is disabled (OCR_ENABLED=false)",),
            processed_page_count=0,
            total_page_count=0,
            languages=config.languages,
            engine_version=None,
        )

    engine_version = get_tesseract_version()
    if engine_version is None:
        return OcrResult(
            status="ocr_unavailable",
            pages=(),
            warnings=("tesseract CLI not found on PATH",),
            processed_page_count=0,
            total_page_count=0,
            languages=config.languages,
            engine_version=None,
        )

    content_hash = hashlib.sha256(pdf_bytes).hexdigest()

    try:
        pdf_document = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:  # noqa: BLE001 - pypdfium2's own error types vary by failure mode
        return OcrResult(
            status="ocr_failed",
            pages=(),
            warnings=(f"could not open PDF for rendering: {exc}",),
            processed_page_count=0,
            total_page_count=0,
            languages=config.languages,
            engine_version=engine_version,
        )

    try:
        total_page_count = len(pdf_document)
        pages_to_process = min(total_page_count, target_page_count)
        is_page_limited = total_page_count > target_page_count

        pages: list[PdfPage] = []
        warnings: list[str] = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for page_index in range(pages_to_process):
                page_number = page_index + 1

                cached_text = cache.get_page_text(content_hash, page_number, languages=config.languages, dpi=config.dpi)
                if cached_text is not None:
                    pages.append(PdfPage(number=page_number, text=cached_text))
                    continue

                image_path = tmp_path / f"page-{page_number}.png"
                try:
                    _render_page_to_png(pdf_document, page_index, dpi=config.dpi, output_path=image_path)
                except Exception as exc:  # noqa: BLE001 - one broken page shouldn't fail the whole document
                    warnings.append(f"page {page_number}: failed to render: {exc}")
                    continue

                try:
                    text = _ocr_image(
                        image_path,
                        tmp_path / f"page-{page_number}",
                        languages=config.languages,
                        timeout_seconds=config.timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    warnings.append(f"page {page_number}: OCR timed out after {config.timeout_seconds}s")
                    continue
                except TesseractUnavailableError:
                    warnings.append(f"page {page_number}: tesseract CLI not found")
                    continue
                except Exception as exc:  # noqa: BLE001 - one broken page shouldn't fail the whole document
                    warnings.append(f"page {page_number}: OCR failed: {exc}")
                    continue

                pages.append(PdfPage(number=page_number, text=text))
                cache.put_page_text(content_hash, page_number, languages=config.languages, dpi=config.dpi, text=text)
    finally:
        pdf_document.close()

    if is_page_limited:
        warnings.append(
            f"document has {total_page_count} page(s); only the first {pages_to_process} "
            f"(target_page_count={target_page_count}) were OCR'd"
        )

    if not pages:
        status: OcrStatus = "ocr_failed"
    elif is_page_limited or len(pages) < pages_to_process:
        status = "ocr_partial"
    else:
        status = "ocr_ok"

    result = OcrResult(
        status=status,
        pages=tuple(pages),
        warnings=tuple(warnings),
        processed_page_count=pages_to_process,
        total_page_count=total_page_count,
        languages=config.languages,
        engine_version=engine_version,
    )

    existing_manifest = cache.get_manifest(content_hash, languages=config.languages, dpi=config.dpi)
    if existing_manifest is None or existing_manifest.get("processed_page_count", 0) <= result.processed_page_count:
        cache.put_manifest(
            content_hash,
            languages=config.languages,
            dpi=config.dpi,
            manifest={
                "status": result.status,
                "warnings": list(result.warnings),
                "processed_page_count": result.processed_page_count,
                "total_page_count": result.total_page_count,
                "languages": result.languages,
                "engine_version": result.engine_version,
            },
        )

    return result
