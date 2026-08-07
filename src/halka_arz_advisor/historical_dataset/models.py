"""The persisted historical-snapshot record and its JSON shape.

:class:`HistoricalIpoSnapshot` bundles three things that must stay
structurally separate (see :mod:`halka_arz_advisor.historical_dataset`'s
module docstring):

1. What a real investor could have known by the decision cutoff — the
   full feature-coverage audit (:attr:`audit_results`, every catalog
   feature's status/evidence, exactly as
   :func:`halka_arz_advisor.decision.audit.audit_company` already
   produces it — nothing re-derived) and the reconstructed
   :attr:`decision_result` (``None`` only when the cutoff itself
   couldn't be resolved — see :mod:`halka_arz_advisor.historical_dataset.cutoff`).
2. Why (:attr:`cutoff`, :attr:`considered_disclosure_ids`/
   :attr:`excluded_post_cutoff_disclosure_ids`) — so a later reviewer
   can audit exactly which documents fed the reconstruction and which
   were excluded as post-cutoff, without re-deriving it.
3. What actually happened afterwards (:attr:`outcome`) — attached last,
   read by nothing above it. No field on this dataclass, and no
   function in :mod:`halka_arz_advisor.historical_dataset.snapshot_builder`,
   ever threads :attr:`outcome` back into (1) or (2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..decision.audit import FeatureAuditResult
from ..decision.engine import DecisionResult
from ..evds.models import MarketContextSnapshot
from ..ipo_outcomes.models import IpoMarketOutcome, outcome_to_dict
from .cutoff import CutoffResolution

HISTORICAL_DATASET_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class HistoricalIpoSnapshot:
    dataset_version: str
    spk_record_id: str
    ticker: str | None
    company_name: str | None

    cutoff: CutoffResolution
    considered_disclosure_ids: tuple[str, ...]
    excluded_post_cutoff_disclosure_ids: tuple[str, ...]

    audit_results: tuple[FeatureAuditResult, ...]
    decision_result: DecisionResult | None
    market_context: MarketContextSnapshot

    outcome: IpoMarketOutcome | None

    generated_at: datetime


def _cutoff_to_dict(cutoff: CutoffResolution) -> dict:
    return {
        "status": cutoff.status,
        "cutoff_date": cutoff.cutoff_date.isoformat() if cutoff.cutoff_date else None,
        "candidate_dates": [d.isoformat() for d in cutoff.candidate_dates],
        "source": cutoff.source,
    }


def _feature_contribution_to_dict(c) -> dict:
    return {
        "category": c.category,
        "feature_id": c.feature_id,
        "status": c.status,
        "raw_value": c.raw_value,
        "normalized_score": c.normalized_score,
        "weight": c.weight,
        "included_in_score": c.included_in_score,
        "evidence": [e.as_dict() for e in c.evidence],
    }


def _decision_result_to_dict(result: DecisionResult) -> dict:
    return {
        "signal": result.signal,
        "total_score": result.total_score,
        "confidence_score": result.confidence_score,
        "rule_version": result.rule_version,
        "weight_set_version": result.weight_set_version,
        "category_scores": [
            {"category": c.category, "score": c.score, "coverage": c.coverage, "status": c.status}
            for c in result.category_scores
        ],
        "hard_rules": [
            {"rule_id": r.rule_id, "target": r.target, "triggered": r.triggered, "reason": r.reason}
            for r in result.hard_rules
        ],
        "warnings": list(result.warnings),
        "feature_contributions": [_feature_contribution_to_dict(c) for c in result.feature_contributions],
    }


def _market_context_to_dict(snapshot: MarketContextSnapshot) -> dict:
    return {
        name: {"value": fv.value, "as_of_date": fv.as_of_date.isoformat(), "source_series_codes": list(fv.source_series_codes)}
        for name, fv in sorted(snapshot.features.items())
    }


def snapshot_to_dict(snapshot: HistoricalIpoSnapshot) -> dict:
    return {
        "dataset_version": snapshot.dataset_version,
        "spk_record_id": snapshot.spk_record_id,
        "ticker": snapshot.ticker,
        "company_name": snapshot.company_name,
        "cutoff": _cutoff_to_dict(snapshot.cutoff),
        "considered_disclosure_ids": list(snapshot.considered_disclosure_ids),
        "excluded_post_cutoff_disclosure_ids": list(snapshot.excluded_post_cutoff_disclosure_ids),
        "audit_results": [r.as_dict() for r in snapshot.audit_results],
        "decision_result": _decision_result_to_dict(snapshot.decision_result) if snapshot.decision_result else None,
        "market_context": _market_context_to_dict(snapshot.market_context),
        "outcome": outcome_to_dict(snapshot.outcome) if snapshot.outcome is not None else None,
        "generated_at": snapshot.generated_at.isoformat(),
    }
