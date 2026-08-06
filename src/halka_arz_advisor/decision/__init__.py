"""Versioned decision-feature coverage catalog, audit, and (from
:mod:`halka_arz_advisor.decision.engine` onward) the first deterministic
IPO decision engine built on top of them.

:mod:`~halka_arz_advisor.decision.catalog` describes what a full IPO
participation decision would ideally draw on (7 categories: fundamental
quality, valuation, offering structure, market context, allocation
efficiency, demand sentiment, data confidence);
:mod:`~halka_arz_advisor.decision.audit` reports which of those planned
features this project's existing KAP/SPK pipeline can currently satisfy.
Both remain deliberately inert on their own: no scoring, no weighting,
no normalization, no new external data sources, no conflict resolution
— a field the extraction layer found conflicting is reported
``CONFLICTED``, both observations intact, never picked between.

:mod:`~halka_arz_advisor.decision.snapshot` and
:mod:`~halka_arz_advisor.decision.engine` are where scoring actually
happens — combining that same audit output (never re-deriving it) into
category scores, a confidence score, hard rules, and one of
``participate``/``limited_participation``/``skip``/``insufficient_data``,
entirely driven by the explicit, versioned ``expert_v0`` configuration
in :mod:`~halka_arz_advisor.decision.scoring_config`.
"""

from .audit import (
    CompanyDecisionInputs,
    FeatureAuditResult,
    FeatureEvidence,
    FeatureStatus,
    audit_company,
)
from .catalog import FEATURE_CATALOG, get_feature, features_by_category
from .engine import DecisionResult, evaluate_decision
from .explain import format_explanation
from .models import CATEGORIES, AvailabilityKind, FeatureCategory, FeatureSpec, OfferTiming
from .scoring_config import ScoringConfig, get_scoring_config
from .snapshot import DecisionSnapshot, build_decision_snapshot

__all__ = [
    "CompanyDecisionInputs",
    "FeatureAuditResult",
    "FeatureEvidence",
    "FeatureStatus",
    "audit_company",
    "FEATURE_CATALOG",
    "get_feature",
    "features_by_category",
    "CATEGORIES",
    "AvailabilityKind",
    "FeatureCategory",
    "FeatureSpec",
    "OfferTiming",
    "DecisionSnapshot",
    "build_decision_snapshot",
    "DecisionResult",
    "evaluate_decision",
    "format_explanation",
    "ScoringConfig",
    "get_scoring_config",
]
