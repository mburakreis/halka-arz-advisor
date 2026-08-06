"""Orchestration: decide which cached Gemini analyses are new or changed
since the last successful send, format them, and hand them to a
``sender`` callable — updating the sent-state only for sends that
actually succeed.

Mirrors :mod:`halka_arz_advisor.notify.check`'s role for SPK records —
kept thin, no scoring, and never calls Gemini itself (see
:func:`halka_arz_advisor.gemini.analysis.lookup_analysis`, a pure cache
read).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..gemini.analysis import lookup_analysis
from ..gemini.cache import AnalysisCache
from ..kap.extraction import ExtractedFacts
from ..kap.models import KapDisclosure
from ..kap.pdf import PdfCache
from .analysis_formatting import format_analysis_notification
from .analysis_identity import analysis_notification_hash
from .analysis_state import SentAnalysesState
from .telegram import TelegramSendError

DELIVERABLE_STATUSES = ("completed", "insufficient_data")

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
    pdf_cache: PdfCache,
    analysis_cache: AnalysisCache,
    model: str,
    prompt_version: str,
    state: SentAnalysesState,
    infer_company_name_and_ticker: CompanyNameAndTicker,
    sender: Sender,
) -> DeliveryResult:
    """For each company: look up its most recent cached analysis (never
    calling Gemini), skip it unless the status is ``completed`` or
    ``insufficient_data``, skip it again if its content hash was already
    sent, then format and hand the message to ``sender``.

    ``state.sent_hashes`` is only updated for a company whose ``sender``
    call *succeeds* — a :class:`~halka_arz_advisor.notify.telegram.TelegramSendError`
    is caught per-company (not re-raised), so one failure doesn't stop
    the rest of the batch and that company is naturally retried on the
    next call with the same (unmodified) state.
    """
    result = DeliveryResult()

    for record_id, facts in company_facts.items():
        disclosures_for_company = disclosures_by_record.get(record_id, [])
        company_name, ticker = infer_company_name_and_ticker(record_id, disclosures_for_company)

        record = lookup_analysis(
            spk_record_id=record_id,
            facts=facts,
            disclosures=disclosures_for_company,
            pdf_cache=pdf_cache,
            analysis_cache=analysis_cache,
            model_name=model,
        )
        if record is None or record.llm_status not in DELIVERABLE_STATUSES:
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
