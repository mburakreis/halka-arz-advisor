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

from collections.abc import Sequence
from datetime import datetime

from ..kap.documents import aggregate_company_facts, aggregate_company_financial_series, infer_company_name_and_ticker
from ..kap.extraction import apply_lower_authority_fallback
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


def _application_identity(record: SpkIpoApplicationRecord) -> str:
    # Mirrors halka_arz_advisor.notify.identity.application_identity
    # exactly — duplicated for the same reason as _ipo_identity above.
    return f"application:{record.company_name}:{record.application_date.isoformat()}"


def resolve_company_identity(
    record_id: str,
    disclosures: list[KapDisclosure],
    *,
    ipo_records: Sequence[SpkIpoRecord] = (),
    application_records: Sequence[SpkIpoApplicationRecord] = (),
) -> tuple[str, str | None]:
    """The authoritative ``(company_name, ticker)`` pair for one matched
    company.

    A matched SPK record is always authoritative when one exists — a
    completed IPO's own ``sirket_unvani``/``borsa_kodu``, or an
    application's own ``company_name`` — never a KAP disclosure's own
    ``company_name``, which for several of this project's target
    document types (price-determination reports, IPO results, trading-
    start notices) is filed by the lead intermediary brokerage or Borsa
    İstanbul itself, not the issuer (see
    :func:`halka_arz_advisor.kap.documents.infer_company_name_and_ticker`'s
    own docstring). ``record_id`` is expected to be exactly the identity
    string :func:`halka_arz_advisor.kap.matching.match_disclosure`
    already assigned as ``matched_spk_record_id`` — either
    :func:`_ipo_identity` or :func:`_application_identity` — so this
    performs the same lookup, not a fresh name-based guess.

    Falls back to :func:`~halka_arz_advisor.kap.documents.infer_company_name_and_ticker`'s
    disclosure-only heuristic only when ``record_id`` matches neither
    pool — a genuinely pre-application company that still has real KAP
    data worth scoring (see :func:`compute_decision_results`).
    """
    ipo_by_identity = {_ipo_identity(record): record for record in ipo_records}
    spk_record = ipo_by_identity.get(record_id)
    if spk_record is not None and spk_record.sirket_unvani:
        ticker = spk_record.borsa_kodu or next((d.ticker for d in disclosures if d.ticker), None)
        return spk_record.sirket_unvani, ticker

    application_by_identity = {_application_identity(record): record for record in application_records}
    application_record = application_by_identity.get(record_id)
    if application_record is not None:
        ticker = next((d.ticker for d in disclosures if d.ticker), None)
        return application_record.company_name, ticker

    return infer_company_name_and_ticker(record_id, disclosures)


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
    supplementary_disclosures: Sequence[KapDisclosure] = (),
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

    ``supplementary_disclosures`` — lower-authority documents (see
    :mod:`halka_arz_advisor.issuer_ir`) — are folded in via
    :func:`~halka_arz_advisor.kap.extraction.apply_lower_authority_fallback`:
    they only ever fill a field ``processed_disclosures`` has genuinely
    nothing for, never override or get averaged into a KAP-derived value
    (extracted or conflicting), and a company present *only* in
    ``supplementary_disclosures`` (no KAP disclosure matched at all yet)
    still gets a result. They otherwise participate fully — coverage/
    hard-rule document-presence checks, financial observations — exactly
    like a KAP disclosure would.
    """
    company_facts = aggregate_company_facts(processed_disclosures)
    company_financials = aggregate_company_financial_series(processed_disclosures)

    supplementary_list = list(supplementary_disclosures)
    supplementary_facts = aggregate_company_facts(supplementary_list)
    supplementary_financials = aggregate_company_financial_series(supplementary_list)

    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for disclosure in processed_disclosures:
        if disclosure.matched_spk_record_id:
            disclosures_by_record.setdefault(disclosure.matched_spk_record_id, []).append(disclosure)

    supplementary_by_record: dict[str, list[KapDisclosure]] = {}
    for disclosure in supplementary_list:
        if disclosure.matched_spk_record_id:
            supplementary_by_record.setdefault(disclosure.matched_spk_record_id, []).append(disclosure)

    ipo_by_identity = {_ipo_identity(record): record for record in ipo_records}

    results: dict[str, DecisionResult] = {}
    for record_id in sorted(set(disclosures_by_record) | set(supplementary_by_record)):
        primary_for_company = disclosures_by_record.get(record_id, [])
        supplementary_for_company = supplementary_by_record.get(record_id, [])
        combined_disclosures = tuple(primary_for_company) + tuple(supplementary_for_company)

        facts = apply_lower_authority_fallback(company_facts.get(record_id), supplementary_facts.get(record_id))
        financial_observations = tuple(company_financials.get(record_id, ())) + tuple(supplementary_financials.get(record_id, ()))

        company_name, _ticker = resolve_company_identity(
            record_id, list(combined_disclosures), ipo_records=ipo_records, application_records=application_records
        )
        inputs = CompanyDecisionInputs(
            spk_record_id=record_id,
            spk_record=ipo_by_identity.get(record_id),
            application_record=_find_application_record(list(combined_disclosures), application_records),
            facts=facts,
            disclosures=combined_disclosures,
            financial_observations=financial_observations,
            company_name=company_name,
        )
        snapshot = build_decision_snapshot(inputs, reference_date=reference_date)
        results[record_id] = evaluate_decision(snapshot)

    return results
