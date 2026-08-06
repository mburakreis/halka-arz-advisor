"""Select page-aware, category-labeled text sections from a company's
cached KAP PDFs for the Ollama prompt.

Two hard rules from the brief, both enforced here:

- **Never send an entire prospectus.** Pages are only included if they
  look relevant to one of eight categories (offering structure, use of
  proceeds, risk factors, financial position, indebtedness, related-party
  transactions, litigation, dividend policy), and the total is capped at
  a fixed character budget.
- **Never re-download.** Text is read purely from the existing
  :class:`~halka_arz_advisor.kap.pdf.PdfCache` — a cache miss just means
  no sections come from that document, not a fetch.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..kap.models import KapDisclosure
from ..kap.pdf import PdfCache, PdfPage, load_pdf_text
from ..kap.text import fold_turkish

DEFAULT_MAX_TOTAL_CHARS = 12_000
DEFAULT_MAX_CHARS_PER_SECTION = 1_500

# (category, folded keywords) — checked in this order; a page is tagged
# with the *first* category whose keyword appears, so a page discussing
# more than one topic isn't duplicated into multiple sections.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("offering_structure", ("halka arz", "talep toplama", "dagitim yontemi", "sermaye artirimi", "ortak satisi")),
    ("use_of_proceeds", ("fon kullanim",)),
    ("risk_factors", ("risk faktorleri", "riskler")),
    ("financial_position", ("finansal durum", "mali durum", "bilanco", "finansal tablolar")),
    ("indebtedness", ("borclanma", "finansal borclar", "yukumlulukler")),
    ("related_party_transactions", ("iliskili taraf",)),
    ("litigation", ("dava", "hukuki takip", "ihtilaf")),
    ("dividend_policy", ("kar payi dagitim", "temettu")),
)

CATEGORIES: tuple[str, ...] = tuple(category for category, _ in _CATEGORY_KEYWORDS)


@dataclass(frozen=True, slots=True)
class ContextSection:
    disclosure_id: str
    document_type: str
    page_number: int
    category: str
    text: str


def read_cached_pdf_pages(obj_id: str, cache: PdfCache) -> tuple[PdfPage, ...]:
    """Read one attachment's page text purely from the local cache.

    Never downloads. Returns an empty tuple if the attachment isn't
    cached, or has no usable text (scanned/empty/malformed) — a cache
    miss is a normal, expected outcome here, not an error.
    """
    cached_bytes = cache.get(obj_id)
    if cached_bytes is None:
        return ()
    document = load_pdf_text(cached_bytes)
    return document.pages if document.status == "ok" else ()


def _categorize_page(page: PdfPage) -> str | None:
    folded = fold_turkish(page.text)
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in folded for keyword in keywords):
            return category
    return None


def select_sections_for_disclosure(
    disclosure: KapDisclosure,
    pages: tuple[PdfPage, ...],
    *,
    max_chars_per_section: int = DEFAULT_MAX_CHARS_PER_SECTION,
) -> list[ContextSection]:
    """Pick the pages relevant to any of the 8 categories out of one
    document's pages — at most one section per page."""
    sections: list[ContextSection] = []
    for page in pages:
        category = _categorize_page(page)
        if category is None:
            continue
        sections.append(
            ContextSection(
                disclosure_id=disclosure.disclosure_id,
                document_type=disclosure.document_type,
                page_number=page.number,
                category=category,
                text=page.text.strip()[:max_chars_per_section],
            )
        )
    return sections


def select_context_sections(
    disclosures: list[KapDisclosure],
    cache: PdfCache,
    *,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    max_chars_per_section: int = DEFAULT_MAX_CHARS_PER_SECTION,
) -> list[ContextSection]:
    """Build the bounded, page-aware context sections for a company's
    matched disclosures, reading text purely from ``cache``.

    Sections are collected in disclosure order, then page order, and
    kept until ``max_total_chars`` would be exceeded — the remainder is
    dropped rather than truncated mid-section, so every included section
    keeps its full (per-section-capped) text intact.
    """
    candidates: list[ContextSection] = []
    for disclosure in disclosures:
        if disclosure.primary_document is None:
            continue
        pages = read_cached_pdf_pages(disclosure.primary_document.obj_id, cache)
        candidates.extend(
            select_sections_for_disclosure(disclosure, pages, max_chars_per_section=max_chars_per_section)
        )

    selected: list[ContextSection] = []
    total_chars = 0
    for section in candidates:
        if total_chars + len(section.text) > max_total_chars:
            break
        selected.append(section)
        total_chars += len(section.text)
    return selected
