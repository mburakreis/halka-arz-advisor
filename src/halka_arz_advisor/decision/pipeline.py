"""Assembles :class:`~halka_arz_advisor.decision.audit.CompanyDecisionInputs`
from the existing KAP/SPK pipeline's output and evaluates the
deterministic decision engine for every matched company — the single
place :mod:`halka_arz_advisor.gemini.analysis`'s cache-key derivation,
``scripts/analyze_pending_ipos.py``, ``scripts/send_pending_analyses.py``,
and ``scripts/validate_decision_engine.py`` all compute a company's
:class:`~halka_arz_advisor.decision.engine.DecisionResult` from, so they
always see the identical result for identical cached data (required for
the Gemini analysis cache key and the Telegram dedup hash to line up
across separate script runs — see
:func:`halka_arz_advisor.decision.engine.decision_signature`).
"""

from __future__ import annotations

from datetime import datetime

from ..kap.documents import aggregate_company_facts, aggregate_company_financial_series
from ..kap.models import KapDisclosure
from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord
from .audit import CompanyDecisionInputs
from .engine import DecisionResult, evaluate_decision
from .snapshot import build_decision_snapshot


def _ipo_identity(record: SpkIpoRecord) -> str:
    # Mirrors halka_arz_advisor.notify.identity.ipo_identity exactly —
    # duplicated (not imported) since that module is notification-state
    # specific and this one has no notification concerns, matching the
    # precedent already set by scripts/audit_decision_coverage.py.
    company_key = record.borsa_kodu or record.sirket_unvani or "unknown"
    return f"ipo:{company_key}:{record.donem or ''}"


def _find_application_record(
    disclosures_for_company: list[KapDisclosure], application_records: tuple[SpkIpoApplicationRecord, ...]
) -> SpkIpoApplicationRecord | None:
    company_names = {d.company_name.strip().upper() for d in disclosures_for_company if d.company_name}
    for record in application_records:
        if record.company_name.strip().upper() in company_names:
            return record
    return None


def compute_decision_results(
    processed_disclosures: list[KapDisclosure],
    *,
    ipo_records: tuple[SpkIpoRecord, ...] = (),
    application_records: tuple[SpkIpoApplicationRecord, ...] = (),
    reference_date: datetime | None = None,
) -> dict[str, DecisionResult]:
    """One :class:`~halka_arz_advisor.decision.engine.DecisionResult` per
    ``matched_spk_record_id`` found in ``processed_disclosures`` (the
    output of :func:`~halka_arz_advisor.kap.documents.process_disclosure_documents`
    for each matched target disclosure) — combining that company's
    aggregated facts, financial observations, and every disclosure
    already processed for it. A company present in ``processed_disclosures``
    but absent from ``ipo_records``/``application_records`` still gets a
    result (with ``spk_record``/``application_record`` left ``None``),
    since a genuinely pre-application company can still have real KAP
    data worth scoring.
    """
    company_facts = aggregate_company_facts(processed_disclosures)
    company_financials = aggregate_company_financial_series(processed_disclosures)

    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for disclosure in processed_disclosures:
        if disclosure.matched_spk_record_id:
            disclosures_by_record.setdefault(disclosure.matched_spk_record_id, []).append(disclosure)

    ipo_by_identity = {_ipo_identity(record): record for record in ipo_records}

    results: dict[str, DecisionResult] = {}
    for record_id, disclosures_for_company in disclosures_by_record.items():
        inputs = CompanyDecisionInputs(
            spk_record_id=record_id,
            spk_record=ipo_by_identity.get(record_id),
            application_record=_find_application_record(disclosures_for_company, application_records),
            facts=company_facts.get(record_id),
            disclosures=tuple(disclosures_for_company),
            financial_observations=company_financials.get(record_id, ()),
        )
        snapshot = build_decision_snapshot(inputs, reference_date=reference_date)
        results[record_id] = evaluate_decision(snapshot)

    return results
