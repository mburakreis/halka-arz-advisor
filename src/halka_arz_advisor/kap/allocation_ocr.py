"""Scoped, on-demand deep-OCR fallback for recovering an IPO's
pre-offer allocation-mechanics fields when their source text sits past
:mod:`halka_arz_advisor.kap.ocr`'s default page budget inside a scanned
base İzahname.

**Why this exists.** ``kap.ocr``'s ``OCR_MAX_PAGES`` (default 30) is
shared infrastructure also tuned for ``price_determination_report``
financial-statement extraction — raising it globally would slow down
every OCR run project-wide for the sake of one late-document section.
Confirmed live (2026-08-08) against GOLDA's cache: its full 7-part
İzahname bundle is already backfilled, but the part containing the
§25.2.3(a) tahsisat (allocation) table only has its OCR text cached up
to page 30, well short of where that section actually sits.

**What this does instead.** Only for a company whose already-built
:class:`~halka_arz_advisor.kap.offering_terms.OfferingTerms` still has
one of :data:`ALLOCATION_TARGET_FIELDS` as ``"not_found"``, and only
against that company's own ``approved_prospectus`` disclosures already
classified :func:`~halka_arz_advisor.kap.classification.classify_prospectus_document_role`
``"base_document"`` (never ``investor_sale_announcement``/``ipo_results``/
``price_determination_report`` — allocation-critical evidence must stay
pre-offer-safe): extend that document's OCR depth in bounded steps
(:data:`DEFAULT_PAGE_STEP` at a time, up to :data:`DEFAULT_MAX_DEEP_PAGES`),
re-run field extraction after every step, and stop as soon as the
target fields resolve (extracted *or* conflicting — a genuine conflict
is a resolved investigation outcome, not something more OCR could fix)
or every candidate document is exhausted.

:func:`~halka_arz_advisor.kap.ocr.ocr_pdf_extend` (not
:func:`~halka_arz_advisor.kap.ocr.ocr_pdf`) does the actual OCR work —
it reuses any page already cached by an earlier, shallower run and only
renders+recognizes pages that are genuinely still missing, and persists
the deeper result so a second run against the same company is instant
(a full cache hit, no Tesseract calls at all). No PDF is ever
downloaded here — only already-cached bytes (:class:`~halka_arz_advisor.kap.pdf.PdfCache`)
are read, matching "no new KAP crawling unless strictly necessary".
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .classification import classify_prospectus_document_role, extract_prospectus_part_number
from .documents import aggregate_company_facts
from .extraction import build_extracted_facts, extract_observations_from_pages
from .models import KapDisclosure
from .ocr import OcrCache, OcrConfig, load_ocr_config_from_env, ocr_pdf_extend
from .offering_terms import OfferingTerms, build_offering_terms
from .pdf import PdfCache

# The three OfferingTerms fields this fallback exists to recover. Kept
# narrow and explicit (not "every OfferingTerms field") since deep OCR
# is comparatively expensive and this is meant to be triggered only for
# the specific fields known to be gated by page-budget truncation, not
# as a general-purpose "OCR more, see what turns up" tool.
ALLOCATION_TARGET_FIELDS: tuple[str, ...] = (
    "investor_group_allocations",
    "retail_allocation_percentage",
    "retail_offered_shares",
)

# A digitally-readable ("ok") PDF is never OCR'd at all (see kap.ocr's
# module docstring) — mirrors kap.documents's own OCR-eligible pdf_status
# set. Only a "scanned"/"empty" prospectus can benefit from this fallback;
# a missing field on an "ok" document is an extraction-pattern gap, not
# an OCR page-budget one, and deep-OCR can't fix that.
_OCR_ELIGIBLE_PDF_STATUSES = frozenset({"scanned", "empty"})

# pdf.py's own module docstring cites a real 204-page prospectus this
# project has seen live; 240 (8 steps of DEFAULT_PAGE_STEP=30) is a
# bounded ceiling comfortably above that real observed case without
# being unbounded, and applies identically to every company/document —
# never a per-ticker page count.
DEFAULT_PAGE_STEP = 30
DEFAULT_MAX_DEEP_PAGES = 240


@dataclass(frozen=True, slots=True)
class AllocationOcrAttempt:
    """One deep-OCR step against one candidate prospectus disclosure."""

    disclosure_id: str
    part_number: int | None
    pages_ocrd: int
    ocr_status: str
    resolved_after: bool


@dataclass(frozen=True, slots=True)
class AllocationOcrRecoveryResult:
    """Outcome of one company's scoped deep-OCR recovery pass.

    ``updated_disclosures`` is the caller's full disclosure list with
    any deep-OCR'd ``approved_prospectus`` entries replaced by their
    re-extracted copies — pass it back through
    :func:`~halka_arz_advisor.kap.documents.aggregate_company_facts` /
    :func:`~halka_arz_advisor.kap.offering_terms.build_offering_terms`
    (already done once here to produce ``offering_terms`` directly, so
    most callers can just use that) to see the effect elsewhere too.
    """

    record_id: str
    already_resolved: bool
    resolved: bool
    attempts: tuple[AllocationOcrAttempt, ...]
    updated_disclosures: tuple[KapDisclosure, ...]
    offering_terms: OfferingTerms


def _fields_unresolved(terms: OfferingTerms) -> bool:
    return any(getattr(terms, name).status == "not_found" for name in ALLOCATION_TARGET_FIELDS)


def _prospectus_candidates(disclosures: list[KapDisclosure], record_id: str) -> list[KapDisclosure]:
    candidates = [
        d
        for d in disclosures
        if d.matched_spk_record_id == record_id
        and d.document_type == "approved_prospectus"
        and d.pdf_status in _OCR_ELIGIBLE_PDF_STATUSES
        and d.primary_document is not None
        and classify_prospectus_document_role(d.summary, d.title) == "base_document"
    ]

    # Prioritize the highest-numbered part first (see
    # extract_prospectus_part_number's docstring: the best generic proxy
    # this project has for "closest to the end of the whole document",
    # where the tahsisat section tends to sit) — parts with no
    # recoverable number sort after every numbered one, in publish order.
    def _sort_key(d: KapDisclosure) -> tuple[bool, int, object]:
        part_number = extract_prospectus_part_number(d.summary, d.title)
        return (part_number is None, -(part_number or 0), d.published_at)

    return sorted(candidates, key=_sort_key)


def _rebuild_terms(disclosures_by_id: dict[str, KapDisclosure], record_id: str) -> OfferingTerms:
    pre_offer_types = {"approved_prospectus", "investor_sale_announcement"}
    all_disclosures = list(disclosures_by_id.values())
    facts = aggregate_company_facts(all_disclosures).get(record_id)
    company_pre_offer = [d for d in all_disclosures if d.matched_spk_record_id == record_id and d.document_type in pre_offer_types]
    return build_offering_terms(facts, company_pre_offer)


def recover_allocation_sections(
    record_id: str,
    disclosures: list[KapDisclosure],
    *,
    pdf_cache: PdfCache,
    ocr_cache: OcrCache,
    ocr_config: OcrConfig | None = None,
    page_step: int = DEFAULT_PAGE_STEP,
    max_deep_pages: int = DEFAULT_MAX_DEEP_PAGES,
) -> AllocationOcrRecoveryResult:
    """Scoped deep-OCR recovery for one company's allocation-mechanics
    fields (see module docstring). Pure with respect to KAP itself (no
    network calls) — only reads already-cached PDF bytes and writes to
    ``ocr_cache``'s on-disk cache.

    Does nothing (returns immediately, ``already_resolved=True``) if
    every :data:`ALLOCATION_TARGET_FIELDS` field is already resolved
    (``"extracted"`` or ``"conflicting"``) from the disclosures as
    given — this fallback is meant to be triggered only when needed,
    never run unconditionally.
    """
    disclosures_by_id = {d.disclosure_id: d for d in disclosures}
    base_config = ocr_config or load_ocr_config_from_env()

    initial_terms = _rebuild_terms(disclosures_by_id, record_id)
    if not _fields_unresolved(initial_terms):
        return AllocationOcrRecoveryResult(
            record_id=record_id,
            already_resolved=True,
            resolved=True,
            attempts=(),
            updated_disclosures=tuple(disclosures_by_id.values()),
            offering_terms=initial_terms,
        )

    candidates = _prospectus_candidates(list(disclosures_by_id.values()), record_id)
    attempts: list[AllocationOcrAttempt] = []
    terms = initial_terms

    for candidate in candidates:
        if not _fields_unresolved(terms):
            break

        pdf_bytes = pdf_cache.get(candidate.primary_document.obj_id)
        if pdf_bytes is None:
            continue  # cache-only: never trigger a fresh KAP download here

        part_number = extract_prospectus_part_number(candidate.summary, candidate.title)
        target = page_step
        while target <= max_deep_pages:
            ocr_result = ocr_pdf_extend(pdf_bytes, config=base_config, cache=ocr_cache, target_page_count=target)

            if ocr_result.pages:
                observations = extract_observations_from_pages(
                    ocr_result.pages,
                    document_type="approved_prospectus",
                    disclosure_id=candidate.disclosure_id,
                    attachment_url=candidate.primary_document.url,
                    extraction_method="ocr",
                )
                new_facts = build_extracted_facts(observations, None)
                updated = replace(
                    candidate,
                    extracted_facts=new_facts,
                    ocr_status=ocr_result.status,
                    ocr_warnings=ocr_result.warnings,
                )
                disclosures_by_id[candidate.disclosure_id] = updated
                candidate = updated

            terms = _rebuild_terms(disclosures_by_id, record_id)
            resolved_now = not _fields_unresolved(terms)
            attempts.append(
                AllocationOcrAttempt(
                    disclosure_id=candidate.disclosure_id,
                    part_number=part_number,
                    pages_ocrd=ocr_result.processed_page_count,
                    ocr_status=ocr_result.status,
                    resolved_after=resolved_now,
                )
            )

            # ocr_result.processed_page_count == total_page_count (the
            # whole document has now been OCR'd) or the OCR pipeline
            # itself is unavailable/failed — further steps on this same
            # document would do no new work, so stop deepening it and
            # move on to the next candidate (if any).
            if resolved_now or ocr_result.status in ("ocr_unavailable", "ocr_failed") or ocr_result.processed_page_count < target:
                break
            target += page_step

    return AllocationOcrRecoveryResult(
        record_id=record_id,
        already_resolved=False,
        resolved=not _fields_unresolved(terms),
        attempts=tuple(attempts),
        updated_disclosures=tuple(disclosures_by_id.values()),
        offering_terms=terms,
    )
