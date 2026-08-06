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
from .extraction import ExtractedFacts, FIELD_NAMES, FieldObservation, build_extracted_facts, extract_observations_from_pages
from .models import KapDisclosure
from .pdf import PdfCache, fetch_and_read_pdf

DEFAULT_CACHE_DIR = Path("data") / "cache" / "kap_pdfs"

# Rule 6 of the brief scopes field extraction to these two document
# types only — a Fiyat Tespit Raporu, for instance, is fetched and its
# attachment resolved like any other target type, but its text is never
# run through the field extractors.
_EXTRACTION_ELIGIBLE_TYPES = frozenset({"approved_prospectus", "investor_sale_announcement"})


def process_disclosure_documents(
    disclosure: KapDisclosure,
    *,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    cache: PdfCache | None = None,
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
    )

    base_update = {
        "attachments": attachments,
        "attachment_urls": tuple(a.url for a in attachments),
        "primary_document": primary,
        "pdf_status": fetch_result.status,
    }

    if fetch_result.status != "ok":
        warning = f"primary attachment PDF status: {fetch_result.status}"
        if fetch_result.error:
            warning += f" ({fetch_result.error})"
        return replace(disclosure, **base_update, extraction_warnings=(warning,))

    if disclosure.document_type not in _EXTRACTION_ELIGIBLE_TYPES:
        return replace(disclosure, **base_update, extraction_warnings=())

    observations = extract_observations_from_pages(
        fetch_result.pages,
        document_type=disclosure.document_type,
        disclosure_id=disclosure.disclosure_id,
        attachment_url=primary.url,
    )
    is_prospectus = disclosure.document_type == "approved_prospectus"
    facts = build_extracted_facts(
        observations if is_prospectus else None,
        observations if not is_prospectus else None,
    )

    warnings = () if observations else ("no target fields matched in the extracted text",)
    return replace(disclosure, **base_update, extracted_facts=facts, extraction_warnings=warnings)


def aggregate_company_facts(disclosures: list[KapDisclosure]) -> dict[str, ExtractedFacts]:
    """Merge per-disclosure ``extracted_facts`` across a company's separate
    prospectus and announcement disclosures, grouped by
    ``matched_spk_record_id`` — implementing rule 8's cross-document
    field-priority and conflict-detection rule, which a single
    :class:`KapDisclosure` (one document) can't express on its own.

    Disclosures with no ``matched_spk_record_id`` or no
    ``extracted_facts`` are skipped. Returns ``{spk_record_id: ExtractedFacts}``.
    """
    prospectus_observations: dict[str, dict[str, FieldObservation]] = {}
    announcement_observations: dict[str, dict[str, FieldObservation]] = {}

    for disclosure in disclosures:
        if disclosure.matched_spk_record_id is None or disclosure.extracted_facts is None:
            continue
        bucket = (
            prospectus_observations
            if disclosure.document_type == "approved_prospectus"
            else announcement_observations
            if disclosure.document_type == "investor_sale_announcement"
            else None
        )
        if bucket is None:
            continue
        company_bucket = bucket.setdefault(disclosure.matched_spk_record_id, {})
        for field_name in FIELD_NAMES:
            fact = getattr(disclosure.extracted_facts, field_name)
            if fact.status == "extracted" and field_name not in company_bucket:
                company_bucket[field_name] = fact.observations[0]

    record_ids = set(prospectus_observations) | set(announcement_observations)
    return {
        record_id: build_extracted_facts(
            prospectus_observations.get(record_id), announcement_observations.get(record_id)
        )
        for record_id in record_ids
    }
