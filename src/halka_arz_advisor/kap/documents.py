"""Connects :mod:`~halka_arz_advisor.kap.attachments`,
:mod:`~halka_arz_advisor.kap.pdf`, and :mod:`~halka_arz_advisor.kap.extraction`
to a :class:`~halka_arz_advisor.kap.models.KapDisclosure`: resolve its
attachments, download+read the primary one, and (for the two document
types rule 6 of the brief scopes field extraction to) extract IPO
participation fields.

Also provides a small cross-disclosure aggregation
(:func:`aggregate_company_facts`) implementing rule 8's "prefer the
announcement for X, the prospectus for Y, never silently overwrite a
conflict" merge *across* a company's separate prospectus and
announcement disclosures — a single disclosure only ever has one
document, so that merge can't happen at the per-disclosure level.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx

from ..probe.config import ProbeConfig
from .attachments import resolve_attachments, select_primary_attachment
from .extraction import (
    FIELD_NAMES,
    ExtractedFacts,
    ExtractionMethod,
    FieldObservation,
    build_extracted_facts,
    extract_observations_from_pages,
)
from .models import KapDisclosure
from .ocr import OcrCache, OcrConfig, load_ocr_config_from_env, ocr_pdf
from .pdf import PdfCache, fetch_and_read_pdf

DEFAULT_CACHE_DIR = Path("data") / "cache" / "kap_pdfs"

# Rule 6 of the brief scopes field extraction to the prospectus and
# investor sale announcement, plus (for the post-offer participation
# fields — see kap.extraction's IPO-results section) the IPO results
# disclosure ("Halka Arzına İlişkin Sonuçlar"). A Fiyat Tespit Raporu,
# for instance, is still fetched and its attachment resolved like any
# other target type, but its text is never run through the field
# extractors.
_EXTRACTION_ELIGIBLE_TYPES = frozenset(
    {"approved_prospectus", "investor_sale_announcement", "ipo_results"}
)

# pdf_status values OCR is attempted for — a digitally-readable PDF
# ("ok") never touches OCR; "malformed"/"unavailable" mean there's no
# usable PDF bytes to render in the first place.
_OCR_ELIGIBLE_PDF_STATUSES = frozenset({"scanned", "empty"})


def process_disclosure_documents(
    disclosure: KapDisclosure,
    *,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    cache: PdfCache | None = None,
    cache_only: bool = False,
    ocr_scanned: bool = False,
    ocr_config: OcrConfig | None = None,
    ocr_cache: OcrCache | None = None,
) -> KapDisclosure:
    """Resolve attachments, download+parse the primary one, and (for
    prospectus/announcement disclosures) extract fields — returning an
    updated copy of ``disclosure``.

    Never raises for a document-level problem (no attachment, scanned or
    malformed PDF, no regex match) — those are recorded via
    ``pdf_status``/``extraction_warnings`` instead, never silently
    dropped. A hard KAP API failure resolving attachments (transport,
    bad response, schema mismatch) does propagate, same as everywhere
    else in this project.

    Attachment *metadata* (which attachments exist, their objId) is
    always resolved live — that's a small JSON call, not a document
    download. If ``cache_only`` is set, the PDF itself is only ever read
    from ``cache``; a cache miss is reported as ``pdf_status="unavailable"``
    rather than triggering a download (used by the Gemini analysis layer,
    which must only analyze documents an earlier ``--parse-documents``
    run already cached).

    If ``ocr_scanned`` is set and the primary PDF's digital text layer
    comes back ``"scanned"``/``"empty"``, :mod:`halka_arz_advisor.kap.ocr`
    is used as a fallback (never for an already-readable ``"ok"`` PDF) —
    the outcome is recorded separately via ``ocr_status``/``ocr_warnings``
    without changing what ``pdf_status`` means. Field extraction runs
    against OCR'd pages exactly as it would against digital ones, tagging
    the resulting observations' provenance ``extraction_method="ocr"``.
    """
    if disclosure.disclosure_index is None:
        return replace(
            disclosure,
            pdf_status="unavailable",
            extraction_warnings=("no disclosure index available to resolve attachments",),
        )

    attachments = tuple(resolve_attachments(disclosure.disclosure_index, config=config, client=client))
    primary = select_primary_attachment(list(attachments))

    if primary is None:
        reason = (
            "no primary attachment found (only signature/cover/appendix/review attachments present)"
            if attachments
            else "disclosure has no attachments"
        )
        return replace(
            disclosure,
            attachments=attachments,
            attachment_urls=tuple(a.url for a in attachments),
            primary_document=None,
            pdf_status="unavailable",
            extraction_warnings=(reason,),
        )

    fetch_result = fetch_and_read_pdf(
        primary.url,
        primary.obj_id,
        disclosure_index=disclosure.disclosure_index,
        config=config,
        client=client,
        cache=cache,
        cache_only=cache_only,
    )

    pages = fetch_result.pages
    extraction_method: ExtractionMethod = "digital"
    ocr_status = None
    ocr_warnings: tuple[str, ...] = ()
    have_usable_pages = fetch_result.status == "ok"

    if ocr_scanned and fetch_result.status in _OCR_ELIGIBLE_PDF_STATUSES:
        pdf_bytes = cache.get(primary.obj_id) if cache is not None else None
        if pdf_bytes is None:
            ocr_status = "ocr_unavailable"
            ocr_warnings = ("OCR requested but the cached PDF bytes were not available to render",)
        else:
            ocr_result = ocr_pdf(pdf_bytes, config=ocr_config or load_ocr_config_from_env(), cache=ocr_cache)
            ocr_status = ocr_result.status
            ocr_warnings = ocr_result.warnings
            if ocr_result.pages:
                pages = ocr_result.pages
                extraction_method = "ocr"
                have_usable_pages = True

    base_update = {
        "attachments": attachments,
        "attachment_urls": tuple(a.url for a in attachments),
        "primary_document": primary,
        "pdf_status": fetch_result.status,
        "ocr_status": ocr_status,
        "ocr_warnings": ocr_warnings,
    }

    if not have_usable_pages:
        warning = f"primary attachment PDF status: {fetch_result.status}"
        if fetch_result.error:
            warning += f" ({fetch_result.error})"
        return replace(disclosure, **base_update, extraction_warnings=(warning,))

    if disclosure.document_type not in _EXTRACTION_ELIGIBLE_TYPES:
        return replace(disclosure, **base_update, extraction_warnings=())

    observations = extract_observations_from_pages(
        pages,
        document_type=disclosure.document_type,
        disclosure_id=disclosure.disclosure_id,
        attachment_url=primary.url,
        extraction_method=extraction_method,
    )
    facts = build_extracted_facts(
        observations if disclosure.document_type == "approved_prospectus" else None,
        observations if disclosure.document_type == "investor_sale_announcement" else None,
        observations if disclosure.document_type == "ipo_results" else None,
    )

    warnings = () if observations else ("no target fields matched in the extracted text",)
    return replace(disclosure, **base_update, extracted_facts=facts, extraction_warnings=warnings)


def aggregate_company_facts(disclosures: list[KapDisclosure]) -> dict[str, ExtractedFacts]:
    """Merge per-disclosure ``extracted_facts`` across a company's separate
    prospectus, announcement, and IPO results disclosures, grouped by
    ``matched_spk_record_id`` — implementing rule 8's cross-document
    field-priority and conflict-detection rule, which a single
    :class:`KapDisclosure` (one document) can't express on its own.

    Disclosures with no ``matched_spk_record_id`` or no
    ``extracted_facts`` are skipped. Returns ``{spk_record_id: ExtractedFacts}``.
    """
    prospectus_observations: dict[str, dict[str, FieldObservation]] = {}
    announcement_observations: dict[str, dict[str, FieldObservation]] = {}
    ipo_results_observations: dict[str, dict[str, FieldObservation]] = {}

    for disclosure in disclosures:
        if disclosure.matched_spk_record_id is None or disclosure.extracted_facts is None:
            continue
        bucket = (
            prospectus_observations
            if disclosure.document_type == "approved_prospectus"
            else announcement_observations
            if disclosure.document_type == "investor_sale_announcement"
            else ipo_results_observations
            if disclosure.document_type == "ipo_results"
            else None
        )
        if bucket is None:
            continue
        company_bucket = bucket.setdefault(disclosure.matched_spk_record_id, {})
        for field_name in FIELD_NAMES:
            fact = getattr(disclosure.extracted_facts, field_name)
            if fact.status == "extracted" and field_name not in company_bucket:
                company_bucket[field_name] = fact.observations[0]

    record_ids = set(prospectus_observations) | set(announcement_observations) | set(ipo_results_observations)
    return {
        record_id: build_extracted_facts(
            prospectus_observations.get(record_id),
            announcement_observations.get(record_id),
            ipo_results_observations.get(record_id),
        )
        for record_id in record_ids
    }
