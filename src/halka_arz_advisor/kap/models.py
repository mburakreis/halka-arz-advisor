"""Normalized model for one KAP disclosure list item.

Field names and shapes below (``disclosureBasic``/``disclosureDetail``,
``disclosureId``, ``publishDate`` as ``DD.MM.YYYY HH:MM:SS``, etc.) were
confirmed against a live response from
``https://www.kap.org.tr/tr/api/disclosure/list/main`` — see
:mod:`halka_arz_advisor.kap.client` for the full provenance write-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .attachments import KapAttachment
from .classification import DocumentType, classify_title
from .exceptions import KapSchemaError
from .extraction import ExtractedFacts, SourceSystem
from .financials import FinancialObservation
from .ocr import OcrStatus
from .pdf import PdfStatus

KAP_NOTIFICATION_URL_TEMPLATE = "https://www.kap.org.tr/tr/Bildirim/{index}"


@dataclass(frozen=True, slots=True)
class KapDisclosure:
    disclosure_id: str
    disclosure_index: int | None
    published_at: datetime
    company_name: str
    ticker: str | None
    title: str
    summary: str
    document_type: DocumentType
    notification_url: str
    attachment_urls: tuple[str, ...]
    matched_spk_record_id: str | None
    match_method: str  # "ticker" | "company_name" | "unmatched"
    raw: dict = field(repr=False)

    # "kap" for everything produced by this module (the only value it
    # ever sets) — a disclosure built instead from an issuer's own
    # investor-relations site (see :mod:`halka_arz_advisor.issuer_ir`)
    # is tagged "issuer_ir" so downstream code (coverage/hard-rule
    # checks, the KAP-authority fallback merge, Telegram/audit display)
    # can tell the two apart without guessing from ``disclosure_id``.
    source_system: SourceSystem = "kap"

    # Populated by halka_arz_advisor.kap.documents.process_disclosure_documents
    # (only when the CLI is run with --parse-documents) — left at these
    # defaults otherwise, so a disclosure that was never sent through
    # document processing is honestly distinguishable from one that was
    # processed and found nothing.
    attachments: tuple[KapAttachment, ...] = ()
    primary_document: KapAttachment | None = None
    pdf_status: PdfStatus | None = None
    extracted_facts: ExtractedFacts | None = None
    extraction_warnings: tuple[str, ...] = ()

    # Populated alongside extracted_facts, only for document types
    # halka_arz_advisor.kap.financials's "Gelir Tablosu" table pattern
    # has been confirmed against (currently price_determination_report
    # only) — see halka_arz_advisor.kap.documents._FINANCIAL_ELIGIBLE_TYPES.
    financial_observations: tuple[FinancialObservation, ...] = ()

    # Populated only when process_disclosure_documents is run with
    # ocr_scanned=True *and* pdf_status ended up "scanned"/"empty" — a
    # digitally-readable PDF never touches OCR, so these stay at their
    # defaults (pdf_status alone keeps its original meaning either way).
    ocr_status: OcrStatus | None = None
    ocr_warnings: tuple[str, ...] = ()


def _single_token(value: object) -> str | None:
    """Return the one token in a comma-separated string, or ``None`` if
    the value is empty/missing/ambiguous (zero or more than one token).

    KAP's ``stockCode``/``relatedStocks`` fields are sometimes a single
    ticker, sometimes a comma-separated list of several unrelated
    tickers (e.g. an intermediary's own dual ticker codes, or several
    companies it has recently handled IPOs for). A multi-valued field
    is exactly the "ambiguous" case this phase's matching rule says not
    to resolve automatically, so it's treated as absent here rather
    than guessed at (e.g. by taking the first token).
    """
    if not value or not isinstance(value, str):
        return None
    tokens = [t.strip() for t in value.split(",") if t.strip()]
    return tokens[0] if len(tokens) == 1 else None


def _extract_ticker(basic: dict) -> str | None:
    """Pick the ticker this disclosure is actually *about*.

    For a company's own filings (e.g. it publishing its own approved
    prospectus), ``stockCode`` is that company's ticker and is correct
    directly. But ``Fiyat Tespit Raporu``/``Halka Arz Sonuçları`` are
    typically filed by the IPO's intermediary brokerage under its *own*
    KAP membership — there, ``stockCode`` is the broker's ticker, not
    the IPO company's, and the actual subject company's ticker is in
    ``relatedStocks`` instead (confirmed against real filings, e.g. a
    "Halka Arz Sonuçları" filed by Garanti Yatırım Menkul Kıymetler
    with ``relatedStocks: "QUICK"`` — actually about Quick Sigorta's
    IPO). ``relatedStocks`` is preferred when it's unambiguous
    (single-valued); otherwise this falls back to ``stockCode``.
    """
    related = _single_token(basic.get("relatedStocks"))
    if related:
        return related
    return _single_token(basic.get("stockCode"))


def parse_disclosure(raw: dict) -> KapDisclosure:
    """Normalize one raw ``{"disclosureBasic": ..., "disclosureDetail": ...}``
    item from the KAP disclosure list response.

    Raises :class:`KapSchemaError` if a required field is missing or of
    the wrong type — never invents a value for a malformed item.
    Matching (``matched_spk_record_id``/``match_method``) is not done
    here; every parsed disclosure starts as ``"unmatched"`` until passed
    through :func:`halka_arz_advisor.kap.matching.match_disclosure`.
    """
    if not isinstance(raw, dict):
        raise KapSchemaError(f"disclosure item is not a JSON object: {type(raw).__name__}")

    basic = raw.get("disclosureBasic")
    if not isinstance(basic, dict):
        raise KapSchemaError("disclosure item is missing a 'disclosureBasic' object")

    disclosure_id = basic.get("disclosureId")
    if not disclosure_id or not isinstance(disclosure_id, str):
        raise KapSchemaError(f"disclosure item has an invalid 'disclosureId': {basic.get('disclosureId')!r}")

    title = basic.get("title")
    if not title or not isinstance(title, str):
        raise KapSchemaError(f"disclosure {disclosure_id} is missing a string 'title'")

    published_raw = basic.get("publishDate")
    if not isinstance(published_raw, str):
        raise KapSchemaError(f"disclosure {disclosure_id} is missing a string 'publishDate'")
    try:
        published_at = datetime.strptime(published_raw, "%d.%m.%Y %H:%M:%S")
    except ValueError as exc:
        raise KapSchemaError(
            f"disclosure {disclosure_id} has an unparsable publishDate {published_raw!r}: {exc}"
        ) from exc

    company_name_raw = basic.get("companyTitle")
    if not isinstance(company_name_raw, str):
        raise KapSchemaError(f"disclosure {disclosure_id} is missing a string 'companyTitle'")
    company_name = company_name_raw.strip()

    summary = basic.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""

    disclosure_index_raw = basic.get("disclosureIndex")
    disclosure_index = disclosure_index_raw if isinstance(disclosure_index_raw, int) else None
    notification_url = (
        KAP_NOTIFICATION_URL_TEMPLATE.format(index=disclosure_index) if disclosure_index is not None else ""
    )

    return KapDisclosure(
        disclosure_id=disclosure_id,
        disclosure_index=disclosure_index,
        published_at=published_at,
        company_name=company_name,
        ticker=_extract_ticker(basic),
        title=title,
        summary=summary,
        document_type=classify_title(title),
        notification_url=notification_url,
        # Real attachment URLs are resolved separately, on demand, via
        # halka_arz_advisor.kap.documents.process_disclosure_documents
        # (only when --parse-documents is requested) — not eagerly for
        # every disclosure here, since that's a second network call per
        # item and most disclosures are never a target document type.
        attachment_urls=(),
        matched_spk_record_id=None,
        match_method="unmatched",
        raw=raw,
    )
