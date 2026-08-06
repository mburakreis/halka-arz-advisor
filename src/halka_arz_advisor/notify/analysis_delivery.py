"""Orchestration: decide which cached Gemini analyses are new or changed
since the last successful send, format them, and hand them to a
``sender`` callable — updating the sent-state only for sends that
actually succeed.

Mirrors :mod:`halka_arz_advisor.notify.check`'s role for SPK records —
kept thin, no scoring, and never calls Gemini itself (see
:func:`halka_arz_advisor.gemini.analysis.lookup_analysis`, a pure cache
read). Deliverability is now driven by whether a
:class:`~halka_arz_advisor.decision.engine.DecisionResult` exists for
the company, not by Gemini's own status — the deterministic result
(and :func:`halka_arz_advisor.decision.explain.format_explanation` as
its narrative fallback) means there's always something worth sending
once a company has *any* matched KAP/SPK data, whether or not Gemini's
narrative is available.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..decision.engine import DecisionResult
from ..gemini.analysis import lookup_analysis
from ..gemini.cache import AnalysisCache
from ..kap.extraction import ExtractedFacts
from ..kap.models import KapDisclosure
from ..kap.ocr import OcrCache, OcrConfig
from ..kap.pdf import PdfCache
from .analysis_formatting import format_analysis_notification
from .analysis_identity import analysis_notification_hash
from .analysis_state import SentAnalysesState
from .telegram import TelegramSendError

# Raises TelegramSendError (or a dry-run stand-in that never raises) on failure.
Sender = Callable[[str], None]
CompanyNameAndTicker = Callable[[str, list[KapDisclosure]], tuple[str, str | None]]


@dataclass(slots=True)
class DeliveryResult:
    sent_record_ids: list[str] = field(default_factory=list)
    skipped_unchanged_record_ids: list[str] = field(default_factory=list)
    skipped_no_analysis_record_ids: list[str] = field(default_factory=list)
    failed_record_ids: list[str] = field(default_factory=list)


def deliver_pending_analyses(
    *,
    company_facts: dict[str, ExtractedFacts],
    disclosures_by_record: dict[str, list[KapDisclosure]],
    decision_results: dict[str, DecisionResult],
    pdf_cache: PdfCache,
    analysis_cache: AnalysisCache,
    model: str,
    prompt_version: str,
    state: SentAnalysesState,
    infer_company_name_and_ticker: CompanyNameAndTicker,
    sender: Sender,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> DeliveryResult:
    """For each company with a computed ``decision_results`` entry: look
    up its most recent cached Gemini analysis (never calling Gemini),
    skip it if its content hash was already sent, then format and hand
    the message to ``sender``.

    A company absent from ``decision_results`` (no matched KAP/SPK data
    at all) is skipped — there's nothing to tell the user. Every company
    present in it is deliverable regardless of Gemini's own status: the
    deterministic result is always the source of truth for the
    signal/scores, and :func:`~halka_arz_advisor.notify.analysis_formatting.format_analysis_notification`
    falls back to a deterministic explanation when Gemini's narrative
    isn't ``"completed"``.

    ``state.sent_hashes`` is only updated for a company whose ``sender``
    call *succeeds* — a :class:`~halka_arz_advisor.notify.telegram.TelegramSendError`
    is caught per-company (not re-raised), so one failure doesn't stop
    the rest of the batch and that company is naturally retried on the
    next call with the same (unmodified) state.
    """
    result = DeliveryResult()

    for record_id, decision_result in decision_results.items():
        facts = company_facts.get(record_id)
        disclosures_for_company = disclosures_by_record.get(record_id, [])
        company_name, ticker = infer_company_name_and_ticker(record_id, disclosures_for_company)

        if facts is None:
            # No readable document produced ExtractedFacts for this
            # company at all (e.g. every cached PDF is scanned/OCR-less)
            # — format_analysis_notification needs a real ExtractedFacts
            # to render price/date/distribution, so there's nothing
            # deliverable yet even though a decision_result exists.
            result.skipped_no_analysis_record_ids.append(record_id)
            continue

        record = lookup_analysis(
            spk_record_id=record_id,
            facts=facts,
            disclosures=disclosures_for_company,
            pdf_cache=pdf_cache,
            analysis_cache=analysis_cache,
            model_name=model,
            decision_result=decision_result,
            ocr_cache=ocr_cache,
            ocr_config=ocr_config,
        )
        if record is None:
            result.skipped_no_analysis_record_ids.append(record_id)
            continue

        notification_hash = analysis_notification_hash(
            spk_record_id=record_id, ticker=ticker, model=model, prompt_version=prompt_version, record=record
        )
        if notification_hash in state.sent_hashes:
            result.skipped_unchanged_record_ids.append(record_id)
            continue

        disclosure_urls = {
            d.disclosure_id: d.notification_url for d in disclosures_for_company if d.notification_url
        }
        message = format_analysis_notification(
            company_name=company_name,
            ticker=ticker,
            facts=facts,
            record=record,
            disclosure_notification_urls=disclosure_urls,
        )

        try:
            sender(message)
        except TelegramSendError:
            result.failed_record_ids.append(record_id)
            continue

        state.sent_hashes.add(notification_hash)
        result.sent_record_ids.append(record_id)

    return result
