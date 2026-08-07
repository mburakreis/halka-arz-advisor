"""Reads cutoff-boundary evidence directly out of official post-offer
documents — deliberately **not** through
:mod:`halka_arz_advisor.kap.documents`'s normal
:class:`~halka_arz_advisor.kap.extraction.ExtractedFacts` pipeline, so
nothing found here can ever accidentally become available as a
``kap_extraction.*`` fact (which would risk it being read as scored
``subscription_window`` feature evidence — live or historical). This is
the one module in :mod:`halka_arz_advisor.historical_dataset` that does
I/O; everything downstream of it
(:func:`halka_arz_advisor.historical_dataset.cutoff.resolve_decision_cutoff`)
takes already-resolved :class:`~halka_arz_advisor.historical_dataset.cutoff.PostOfferCutoffEvidence`
as plain data.

Cache-only by design (``cache_only=True`` on every PDF read): this
module never downloads a new document — it only reads whatever the
normal fetch/hydrate pipeline (``scripts/build_historical_ipo_dataset.py``,
optionally with ``--hydrate``) has already cached. A company with no
cached IPO-results/issuer-IR document simply contributes no evidence at
this tier, exactly like a genuine "not found."
"""

from __future__ import annotations

from collections.abc import Sequence

from ..kap.attachments import resolve_attachments, select_primary_attachment
from ..kap.extraction import extract_subscription_dates, extract_subscription_end_date_from_result_text
from ..kap.models import KapDisclosure
from ..kap.ocr import OcrCache, OcrConfig, ocr_pdf
from ..kap.pdf import PdfCache, fetch_and_read_pdf
from ..probe.config import ProbeConfig
from .cutoff import PostOfferCutoffEvidence

_OCR_ELIGIBLE_STATUSES = frozenset({"scanned", "empty"})


def _read_disclosure_text(
    disclosure: KapDisclosure,
    *,
    config: ProbeConfig | None,
    pdf_cache: PdfCache,
    ocr_cache: OcrCache | None,
    ocr_config: OcrConfig | None,
) -> str:
    """Every page's text, digital or OCR'd — cache-only, so a document
    never downloaded by an earlier run simply yields ``""``.

    A KAP-sourced disclosure's attachment is resolved the normal way
    (one small, already-standard live attachment-metadata call, exactly
    like :func:`halka_arz_advisor.kap.documents.process_disclosure_documents`
    itself makes — this is not a new crawl). An issuer-IR-sourced
    disclosure carries its own direct URL already (see
    :mod:`halka_arz_advisor.issuer_ir.ingest`) and its cache key is
    embedded in its own ``disclosure_id`` (``"issuer_ir:<ticker>:<obj_id>"``
    — see that module's own id format), so no attachment resolution is
    needed or possible for it.
    """
    if disclosure.source_system == "issuer_ir":
        if not disclosure.attachment_urls:
            return ""
        obj_id = disclosure.disclosure_id.rsplit(":", 1)[-1]
        url = disclosure.attachment_urls[0]
    else:
        if disclosure.disclosure_index is None:
            return ""
        attachments = resolve_attachments(disclosure.disclosure_index, config=config)
        primary = select_primary_attachment(attachments)
        if primary is None:
            return ""
        obj_id = primary.obj_id
        url = primary.url

    fetch_result = fetch_and_read_pdf(url, obj_id, config=config, cache=pdf_cache, cache_only=True)
    pages = fetch_result.pages
    if fetch_result.status in _OCR_ELIGIBLE_STATUSES and ocr_config is not None:
        pdf_bytes = pdf_cache.get(obj_id)
        if pdf_bytes is not None:
            ocr_result = ocr_pdf(pdf_bytes, config=ocr_config, cache=ocr_cache)
            pages = ocr_result.pages
    return "\n".join(page.text for page in pages)


def collect_post_offer_cutoff_evidence(
    kap_ipo_results_disclosures: Sequence[KapDisclosure],
    issuer_ir_disclosures: Sequence[KapDisclosure] = (),
    *,
    config: ProbeConfig | None = None,
    pdf_cache: PdfCache,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> tuple[PostOfferCutoffEvidence, ...]:
    """Tier-2 evidence from every ``kap_ipo_results_disclosures`` entry
    (matched via :func:`~halka_arz_advisor.kap.extraction.extract_subscription_end_date_from_result_text`,
    the Turkish-calendar-date IPO-results pattern) plus tier-3 evidence
    from every ``issuer_ir_disclosures`` entry (matched via
    :func:`~halka_arz_advisor.kap.extraction.extract_subscription_dates`,
    the same DD.MM.YYYY-oriented pattern tier 1 itself uses — issuer-IR
    documents are copies of the *pre-offer* announcement/prospectus, not
    IPO-results reports, so they share tier 1's date shape, not tier
    2's). A document contributing nothing parseable simply contributes
    no evidence — never a guess.
    """
    evidence: list[PostOfferCutoffEvidence] = []

    for d in kap_ipo_results_disclosures:
        text = _read_disclosure_text(d, config=config, pdf_cache=pdf_cache, ocr_cache=ocr_cache, ocr_config=ocr_config)
        if not text:
            continue
        found = extract_subscription_end_date_from_result_text(text)
        if found is not None:
            value, snippet = found
            evidence.append(
                PostOfferCutoffEvidence(
                    cutoff_date=value, source="kap_ipo_results.subscription_end_date",
                    disclosure_id=d.disclosure_id, snippet=snippet,
                )
            )

    for d in issuer_ir_disclosures:
        text = _read_disclosure_text(d, config=config, pdf_cache=pdf_cache, ocr_cache=ocr_cache, ocr_config=ocr_config)
        if not text:
            continue
        _start, end = extract_subscription_dates(text)
        if end is not None:
            value, snippet = end
            evidence.append(
                PostOfferCutoffEvidence(
                    cutoff_date=value, source="issuer_ir.subscription_end_date",
                    disclosure_id=d.disclosure_id, snippet=snippet,
                )
            )

    return tuple(evidence)
