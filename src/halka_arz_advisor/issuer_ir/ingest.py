"""Source-agnostic ingestion of one issuer's investor-relations PDFs
into the existing KAP/decision pipeline.

Reuses every downstream piece unmodified: PDF download+cache
(:mod:`halka_arz_advisor.kap.pdf`), OCR fallback
(:mod:`halka_arz_advisor.kap.ocr`), field/financial extraction
(:mod:`halka_arz_advisor.kap.extraction` /
:mod:`halka_arz_advisor.kap.financials`), and the
:class:`~halka_arz_advisor.kap.models.KapDisclosure` shape the
aggregation/decision layer already consumes — nothing here duplicates
that logic, and no LLM is involved anywhere in discovery, classification,
or extraction.

Two entry points, split by cost exactly like
:mod:`halka_arz_advisor.kap.backfill`:

- :func:`search_and_ingest` — the expensive path (a real page fetch),
  only when still needed.
- :func:`reprocess_ingested_documents` — cheap, crawl-free: re-attaches
  whatever a prior run already found and cached.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import httpx

from ..kap.classification import DocumentType
from ..kap.extraction import build_extracted_facts, extract_observations_from_pages
from ..kap.financials import extract_financial_observations_from_pages
from ..kap.matching import normalize_company_name
from ..kap.models import KapDisclosure
from ..kap.ocr import OcrCache, OcrConfig, load_ocr_config_from_env, ocr_pdf
from ..kap.pdf import PdfCache, fetch_and_read_pdf
from ..notify.identity import application_identity, ipo_identity
from ..probe.config import ProbeConfig
from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord
from .cache import IngestedIssuerDocument, IssuerIrCache, IssuerIrCacheEntry
from .crawler import SUPPORTED_ISSUER_IR_DOCUMENT_TYPES, DiscoveredLink, discover_pdf_links, fetch_issuer_ir_page
from .registry import IssuerIrSource, get_issuer_ir_source, registered_tickers

# Field/financial extraction is attempted for every ingested type — the
# same broad-attempt convention halka_arz_advisor.kap.documents already
# uses for its own _EXTRACTION_ELIGIBLE_TYPES (a field simply isn't
# found, honestly, if the document doesn't contain it; nothing here
# assumes financial_statement_attachment/use_of_proceeds_report share
# approved_prospectus's exact layout).
_FINANCIAL_ELIGIBLE_TYPES = frozenset({"price_determination_report", "financial_statement_attachment"})

# Re-crawl a ticker's IPO page at most this often even if something
# supported is still missing — an issuer's page can genuinely gain new
# attachments over time, but a scheduled run must never repeatedly
# crawl the same page every cycle.
RECRAWL_COOLDOWN_HOURS = 24


@dataclass(frozen=True, slots=True)
class IssuerIrIngestOutcome:
    ticker: str
    disclosures: tuple[KapDisclosure, ...]
    recovered_document_types: tuple[DocumentType, ...]
    duplicate_of_known_content: tuple[str, ...]  # URLs skipped as byte-identical to something already known
    crawled: bool


def _obj_id_for_url(url: str) -> str:
    return "issuer_ir-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _process_discovered_link(
    link: DiscoveredLink,
    *,
    record_id: str,
    ticker: str,
    config: ProbeConfig | None,
    client: httpx.Client | None,
    pdf_cache: PdfCache,
    cache_only: bool,
    ocr_scanned: bool,
    ocr_cache: OcrCache | None,
    ocr_config: OcrConfig | None,
) -> KapDisclosure:
    """Build and process one issuer_ir-sourced KapDisclosure for a
    discovered link — reusing kap.pdf's download+cache and, for field/
    financial extraction, exactly the same functions
    halka_arz_advisor.kap.documents.process_disclosure_documents calls
    for a KAP disclosure. Not process_disclosure_documents itself: that
    function starts from a KAP disclosure_index and resolves attachments
    via KAP's own API, which has no equivalent here — the discovered PDF
    URL *is* the attachment."""
    obj_id = _obj_id_for_url(link.url)
    disclosure_id = f"issuer_ir:{ticker}:{obj_id}"

    fetch_result = fetch_and_read_pdf(
        link.url, obj_id, config=config, client=client, cache=pdf_cache, cache_only=cache_only
    )

    disclosure = KapDisclosure(
        disclosure_id=disclosure_id,
        disclosure_index=None,
        # Naive, matching how halka_arz_advisor.kap.models parses a KAP
        # disclosure's own publishDate (no timezone in that format
        # either) — mixing an aware value in here breaks
        # decision.engine's document_freshness component, which compares
        # every disclosure's published_at against the same-convention
        # snapshot.generated_at. No real publish date is visible on an
        # issuer's own page for these links, so "now" (crawl time) is
        # the honest choice — never a guessed historical date.
        published_at=datetime.now(),
        company_name=ticker,
        ticker=ticker,
        title=link.link_text or link.document_type,
        summary="",
        document_type=link.document_type,
        notification_url=link.url,
        attachment_urls=(link.url,),
        matched_spk_record_id=record_id,
        match_method="issuer_ir",
        raw={},
        source_system="issuer_ir",
        pdf_status=fetch_result.status,
    )

    pages = fetch_result.pages
    have_usable_pages = fetch_result.status == "ok"
    ocr_status = None
    ocr_warnings: tuple[str, ...] = ()
    extraction_method = "digital"

    if not have_usable_pages and ocr_scanned and fetch_result.status in ("scanned", "empty"):
        pdf_bytes = pdf_cache.get(obj_id)
        if pdf_bytes is not None:
            ocr_result = ocr_pdf(pdf_bytes, config=ocr_config or load_ocr_config_from_env(), cache=ocr_cache)
            ocr_status = ocr_result.status
            ocr_warnings = ocr_result.warnings
            if ocr_result.pages:
                pages = ocr_result.pages
                have_usable_pages = True
                extraction_method = "ocr"

    disclosure = replace(disclosure, ocr_status=ocr_status, ocr_warnings=ocr_warnings)

    if not have_usable_pages:
        return disclosure

    observations = extract_observations_from_pages(
        pages,
        document_type=link.document_type,
        disclosure_id=disclosure_id,
        attachment_url=link.url,
        extraction_method=extraction_method,
        source_system="issuer_ir",
    )
    facts = build_extracted_facts(
        observations if link.document_type == "approved_prospectus" else None,
        observations if link.document_type == "investor_sale_announcement" else None,
        None,
        observations if link.document_type in ("price_determination_report", "financial_statement_attachment", "use_of_proceeds_report") else None,
    )

    financial_observations = ()
    if link.document_type in _FINANCIAL_ELIGIBLE_TYPES:
        financial_observations = extract_financial_observations_from_pages(
            pages,
            document_type=link.document_type,
            disclosure_id=disclosure_id,
            attachment_url=link.url,
            extraction_method=extraction_method,
            source_system="issuer_ir",
        )

    return replace(disclosure, extracted_facts=facts, financial_observations=financial_observations)


def reprocess_ingested_documents(
    ticker: str,
    cache: IssuerIrCache,
    *,
    record_id: str | None = None,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    pdf_cache: PdfCache,
    ocr_scanned: bool = False,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> list[KapDisclosure]:
    """Re-materialize whatever a prior :func:`search_and_ingest` call
    already found and cached for ``ticker`` — the PDF is already in
    ``pdf_cache``, so this never crawls or re-downloads anything. Safe
    to call on every run."""
    entry = cache.get(ticker)
    if entry is None or not entry.ingested:
        return []
    return [
        _process_discovered_link(
            DiscoveredLink(url=doc.url, link_text=doc.link_text, document_type=doc.document_type),
            record_id=record_id or doc.matched_spk_record_id,
            ticker=ticker,
            config=config,
            client=client,
            pdf_cache=pdf_cache,
            cache_only=True,
            ocr_scanned=ocr_scanned,
            ocr_cache=ocr_cache,
            ocr_config=ocr_config,
        )
        for doc in entry.ingested
    ]


def search_and_ingest(
    record_id: str,
    source: IssuerIrSource,
    missing_types: Sequence[DocumentType],
    *,
    cache: IssuerIrCache,
    pdf_cache: PdfCache,
    known_content_hashes: frozenset[str] = frozenset(),
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    ocr_scanned: bool = False,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
    reference_date: datetime | None = None,
) -> IssuerIrIngestOutcome:
    """Ingest ``source``'s IPO page for whatever of ``missing_types`` is
    still genuinely missing.

    ``known_content_hashes`` — typically the content hashes of this
    company's own already-cached KAP PDFs — lets a byte-identical
    issuer-site copy of a document KAP already has be recognized and
    skipped rather than double-counted as a new recovery (still counts
    as confirmation the two sources agree, but adds nothing new).
    """
    now = reference_date or datetime.now(UTC)
    entry = cache.get(source.ticker)

    reprocessed = reprocess_ingested_documents(
        source.ticker, cache, record_id=record_id, config=config, client=client, pdf_cache=pdf_cache,
        ocr_scanned=ocr_scanned, ocr_cache=ocr_cache, ocr_config=ocr_config,
    )
    already_ingested_types = {d.document_type for d in (entry.ingested if entry else ())}

    still_missing = [
        t for t in missing_types if t in SUPPORTED_ISSUER_IR_DOCUMENT_TYPES and t not in already_ingested_types
    ]

    recently_crawled = entry is not None and (now - entry.crawled_at) < timedelta(hours=RECRAWL_COOLDOWN_HOURS)
    if not still_missing or recently_crawled:
        return IssuerIrIngestOutcome(source.ticker, tuple(reprocessed), (), (), False)

    html = fetch_issuer_ir_page(source, config=config, client=client)
    if html is None:
        # Unreachable/non-HTML — not an error, just nothing found this
        # run; still record the attempt so RECRAWL_COOLDOWN_HOURS applies.
        cache.put(
            source.ticker,
            IssuerIrCacheEntry(
                ticker=source.ticker, crawled_at=now, discovered_link_count=entry.discovered_link_count if entry else 0,
                ingested=entry.ingested if entry else (),
            ),
        )
        return IssuerIrIngestOutcome(source.ticker, tuple(reprocessed), (), (), True)

    links = discover_pdf_links(html, source.ipo_page_url, source.allowed_domain)
    wanted_links = [link for link in links if link.document_type in still_missing]

    known_hashes = set(known_content_hashes)
    for doc in entry.ingested if entry else ():
        known_hashes.add(doc.content_hash)

    newly_ingested: list[IngestedIssuerDocument] = []
    newly_found: list[KapDisclosure] = []
    duplicates: list[str] = []
    recovered_types: set[DocumentType] = set()

    for link in wanted_links:
        if link.document_type in recovered_types:
            continue
        disclosure = _process_discovered_link(
            link, record_id=record_id, ticker=source.ticker, config=config, client=client, pdf_cache=pdf_cache,
            cache_only=False, ocr_scanned=ocr_scanned, ocr_cache=ocr_cache, ocr_config=ocr_config,
        )
        if disclosure.pdf_status != "ok" and disclosure.ocr_status not in ("ocr_ok", "ocr_partial"):
            continue

        pdf_bytes = pdf_cache.get(_obj_id_for_url(link.url))
        content_hash = hashlib.sha256(pdf_bytes).hexdigest() if pdf_bytes is not None else _obj_id_for_url(link.url)

        if content_hash in known_hashes:
            duplicates.append(link.url)
            continue
        known_hashes.add(content_hash)

        newly_found.append(disclosure)
        newly_ingested.append(
            IngestedIssuerDocument(
                url=link.url, link_text=link.link_text, document_type=link.document_type,
                obj_id=_obj_id_for_url(link.url), content_hash=content_hash, matched_spk_record_id=record_id,
            )
        )
        recovered_types.add(link.document_type)

    cache.put(
        source.ticker,
        IssuerIrCacheEntry(
            ticker=source.ticker,
            crawled_at=now,
            discovered_link_count=len(links),
            ingested=tuple(entry.ingested if entry else ()) + tuple(newly_ingested),
        ),
    )

    return IssuerIrIngestOutcome(
        source.ticker, tuple(reprocessed) + tuple(newly_found), tuple(sorted(recovered_types)), tuple(duplicates), True
    )


def resolve_registered_record_id(
    source: IssuerIrSource,
    *,
    ipo_records: Sequence[SpkIpoRecord],
    application_records: Sequence[SpkIpoApplicationRecord],
) -> str | None:
    """The matched-SPK-record identity for a registered issuer_ir
    source — a completed IPO's own ticker first (authoritative, exact),
    falling back to a company-name match against application records
    (reusing :func:`halka_arz_advisor.kap.matching.normalize_company_name`,
    the same normalization the rest of this project's matching already
    uses) for a company that hasn't completed its IPO yet. ``None`` if
    neither pool has a matching record — nothing to associate discovered
    documents with yet."""
    record_id = next(
        (ipo_identity(r) for r in ipo_records if (r.borsa_kodu or "").strip().upper() == source.ticker), None
    )
    if record_id is not None:
        return record_id
    target_name = normalize_company_name(source.company_name)
    return next(
        (application_identity(r) for r in application_records if normalize_company_name(r.company_name) == target_name),
        None,
    )


def collect_supplementary_disclosures(
    *,
    ipo_records: Sequence[SpkIpoRecord] = (),
    application_records: Sequence[SpkIpoApplicationRecord] = (),
    cache: IssuerIrCache,
    pdf_cache: PdfCache,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    ocr_scanned: bool = False,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> list[KapDisclosure]:
    """Cheap, crawl-free: for every registered issuer_ir ticker with a
    matched SPK record, re-attach whatever a prior
    ``scripts/ingest_issuer_ir_documents.py`` run already found and
    cached. This is exactly what a consumer script (analyze/send/
    validate) should pass as
    :func:`halka_arz_advisor.decision.pipeline.compute_decision_results`'s
    ``supplementary_disclosures`` — never performs a fresh crawl itself.
    """
    result: list[KapDisclosure] = []
    for ticker in registered_tickers():
        source = get_issuer_ir_source(ticker)
        if source is None:
            continue
        record_id = resolve_registered_record_id(source, ipo_records=ipo_records, application_records=application_records)
        if record_id is None:
            continue
        result.extend(
            reprocess_ingested_documents(
                ticker, cache, record_id=record_id, config=config, client=client, pdf_cache=pdf_cache,
                ocr_scanned=ocr_scanned, ocr_cache=ocr_cache, ocr_config=ocr_config,
            )
        )
    return result
