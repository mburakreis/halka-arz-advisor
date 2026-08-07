"""Builds one company's :class:`~halka_arz_advisor.historical_dataset.models.HistoricalIpoSnapshot` —
the only function in this package that actually reconstructs a
point-in-time decision. Every other module here is a pure helper this
one composes.

Reuses the existing pipeline exactly as :func:`halka_arz_advisor.decision.pipeline.compute_decision_results`
does — :class:`~halka_arz_advisor.decision.audit.CompanyDecisionInputs`,
:func:`~halka_arz_advisor.decision.snapshot.build_decision_snapshot`,
:func:`~halka_arz_advisor.decision.engine.evaluate_decision` — with the
sole difference being *which* facts/disclosures/application record/
market context are fed in: only what
:mod:`halka_arz_advisor.historical_dataset.cutoff`/``filtering`` prove
were available on or before the resolved cutoff. No scoring weight,
threshold, or rule is touched or re-implemented here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ..decision.audit import CompanyDecisionInputs
from ..decision.engine import evaluate_decision
from ..decision.snapshot import build_decision_snapshot
from ..evds.cache import EvdsCache
from ..evds.models import MarketContextSnapshot
from ..ipo_outcomes.models import IpoMarketOutcome
from ..kap.documents import aggregate_company_facts, aggregate_company_financial_series
from ..kap.models import KapDisclosure
from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord
from .cutoff import PostOfferCutoffEvidence, resolve_decision_cutoff
from .filtering import application_record_before_cutoff, disclosures_before_cutoff, end_of_day_istanbul, market_context_as_of
from .models import HISTORICAL_DATASET_VERSION, HistoricalIpoSnapshot


def build_historical_snapshot(
    record_id: str,
    *,
    ticker: str | None,
    spk_record: SpkIpoRecord | None,
    application_record: SpkIpoApplicationRecord | None,
    disclosures: Sequence[KapDisclosure],
    evds_cache: EvdsCache,
    post_offer_cutoff_evidence: Sequence[PostOfferCutoffEvidence] = (),
    outcome: IpoMarketOutcome | None = None,
    generated_at: datetime | None = None,
) -> HistoricalIpoSnapshot:
    """``disclosures`` should be every KAP-sourced disclosure (including
    any already-cached historical backfill — see
    :func:`halka_arz_advisor.kap.backfill.merge_backfilled_disclosures`)
    matched to ``record_id``, **unfiltered** — this function determines
    the cutoff and does the filtering itself. Deliberately does not
    accept issuer-IR-sourced supplementary disclosures here at all
    (only via ``post_offer_cutoff_evidence``, see below): those are
    stamped with the crawl time, not a real publish date (see
    :mod:`halka_arz_advisor.issuer_ir.ingest`), so this project has no
    way to prove one was available before any given historical cutoff
    *as a feature source*.

    ``post_offer_cutoff_evidence`` — already-extracted, already-resolved
    (see :mod:`halka_arz_advisor.historical_dataset.post_offer_evidence`)
    — supplies :func:`~halka_arz_advisor.historical_dataset.cutoff.resolve_decision_cutoff`'s
    tiers 2/3: an explicit subscription-date restatement in an official
    post-offer document (a KAP IPO-results notice, or an issuer-IR copy
    of the pre-offer announcement). Structurally separate from
    ``disclosures``/``facts`` by construction — this argument only ever
    reaches ``resolve_decision_cutoff``, never
    :class:`~halka_arz_advisor.decision.audit.CompanyDecisionInputs`, so
    it can only move *where* the cutoff falls, never what a snapshot's
    features/decision are computed from.

    ``spk_record`` is used only for identity (``company_name``, via
    ``sirket_unvani``) and to hand through to a later
    :func:`halka_arz_advisor.ipo_outcomes.builder.build_ipo_market_outcome`
    call for the *outcome* label — never as a ``spk_ipo_record.*``
    feature source for the reconstructed decision (see this package's
    module docstring for why).

    ``outcome`` — if given — is attached to the result completely
    as-is, after ``decision_result``/``audit_results`` are already
    final; nothing above this function's own return statement reads it.
    """
    gen_at = generated_at or datetime.now(UTC)
    company_name = spk_record.sirket_unvani if spk_record is not None else None
    all_disclosures = tuple(disclosures)

    unfiltered_facts = aggregate_company_facts(list(all_disclosures)).get(record_id)
    cutoff = resolve_decision_cutoff(unfiltered_facts, post_offer_evidence=post_offer_cutoff_evidence)

    if cutoff.status != "resolved" or cutoff.cutoff_date is None:
        return HistoricalIpoSnapshot(
            dataset_version=HISTORICAL_DATASET_VERSION,
            spk_record_id=record_id,
            ticker=ticker,
            company_name=company_name,
            cutoff=cutoff,
            considered_disclosure_ids=(),
            excluded_post_cutoff_disclosure_ids=tuple(sorted(d.disclosure_id for d in all_disclosures)),
            audit_results=(),
            decision_result=None,
            market_context=MarketContextSnapshot(),
            outcome=outcome,
            generated_at=gen_at,
        )

    cutoff_end_of_day = end_of_day_istanbul(cutoff.cutoff_date)
    considered = disclosures_before_cutoff(all_disclosures, cutoff_end_of_day)
    considered_ids = {d.disclosure_id for d in considered}
    excluded = tuple(sorted(d.disclosure_id for d in all_disclosures if d.disclosure_id not in considered_ids))

    filtered_facts = aggregate_company_facts(list(considered)).get(record_id)
    filtered_financials = aggregate_company_financial_series(list(considered)).get(record_id, ())
    filtered_application = application_record_before_cutoff(application_record, cutoff.cutoff_date)
    market_context = market_context_as_of(evds_cache, cutoff.cutoff_date)

    inputs = CompanyDecisionInputs(
        spk_record_id=record_id,
        spk_record=None,
        application_record=filtered_application,
        facts=filtered_facts,
        disclosures=considered,
        financial_observations=filtered_financials,
        company_name=company_name,
        market_context=market_context,
    )
    decision_snapshot = build_decision_snapshot(inputs, reference_date=cutoff_end_of_day)
    decision_result = evaluate_decision(decision_snapshot)

    return HistoricalIpoSnapshot(
        dataset_version=HISTORICAL_DATASET_VERSION,
        spk_record_id=record_id,
        ticker=ticker,
        company_name=company_name,
        cutoff=cutoff,
        considered_disclosure_ids=tuple(sorted(considered_ids)),
        excluded_post_cutoff_disclosure_ids=excluded,
        audit_results=decision_snapshot.audit_results,
        decision_result=decision_result,
        market_context=market_context,
        outcome=outcome,
        generated_at=gen_at,
    )
