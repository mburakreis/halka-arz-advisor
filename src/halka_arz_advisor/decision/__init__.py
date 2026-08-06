"""Versioned decision-feature coverage catalog and audit.

Describes what a full IPO participation decision would ideally draw on
(:mod:`halka_arz_advisor.decision.catalog` — 7 categories: fundamental
quality, valuation, offering structure, market context, allocation
efficiency, demand sentiment, data confidence) and reports which of
those planned features this project's existing KAP/SPK pipeline can
currently satisfy (:mod:`halka_arz_advisor.decision.audit`).

Deliberately inert: no scoring, no weighting, no normalization, no new
external data sources, no conflict resolution. A field the deterministic
extraction layer already found conflicting stays reported as
``CONFLICTED`` here, both observations intact — this package only ever
*reports* coverage, never decides anything.
"""

from .audit import (
    CompanyDecisionInputs,
    FeatureAuditResult,
    FeatureEvidence,
    FeatureStatus,
    audit_company,
)
from .catalog import FEATURE_CATALOG, get_feature, features_by_category
from .models import CATEGORIES, AvailabilityKind, FeatureCategory, FeatureSpec, OfferTiming

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
]
