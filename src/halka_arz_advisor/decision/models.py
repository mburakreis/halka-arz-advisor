"""Data shapes for the decision-feature coverage catalog and its audit
results.

Deliberately inert: no scoring, no weighting, no normalization, no
conflict resolution. A :class:`FeatureSpec` only *describes* what a
planned decision-support feature would need; the audit
(:mod:`halka_arz_advisor.decision.audit`) only *reports* whether that
need is currently met by data this project's existing KAP/SPK pipeline
already produces — it never fetches anything new and never invents a
value the deterministic extraction layer didn't find.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FeatureCategory = Literal[
    "fundamental_quality",
    "valuation",
    "offering_structure",
    "market_context",
    "allocation_efficiency",
    "demand_sentiment",
    "data_confidence",
]

CATEGORIES: tuple[FeatureCategory, ...] = (
    "fundamental_quality",
    "valuation",
    "offering_structure",
    "market_context",
    "allocation_efficiency",
    "demand_sentiment",
    "data_confidence",
)

# When a feature's underlying data becomes knowable relative to the
# subscription window: "pre_offer" data exists before/during it (from
# the prospectus, the investor sale announcement, or a filed
# application); "post_offer" data only exists once SPK publishes the
# completed-IPO record or a result/listing document is filed.
OfferTiming = Literal["pre_offer", "post_offer"]

# "direct": the feature *is* one already-extracted field, used as-is.
# "derived": the feature is computed from one or more other features'
# values (see FeatureSpec.derivation_dependencies) — arithmetic or a
# simple presence/agreement check only, never a scoring formula.
AvailabilityKind = Literal["direct", "derived"]


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """One planned decision-support feature's requirements — not its
    value. See :mod:`halka_arz_advisor.decision.catalog` for the actual
    catalog and :mod:`halka_arz_advisor.decision.audit` for how a spec
    is evaluated against real company data.
    """

    feature_id: str
    category: FeatureCategory
    title: str
    description: str

    # Namespaced source-field identifiers this feature reads directly
    # (empty for a purely "derived" feature) — see
    # halka_arz_advisor.decision.catalog's module docstring for the
    # namespacing convention (e.g. "kap_extraction.offering_price",
    # "spk_ipo_record.halka_arz_fiyati_tl", "kap_document.ipo_results").
    required_source_fields: tuple[str, ...]

    # KAP document types and/or SPK data sources allowed to supply
    # those fields — informational (what *could* satisfy this feature),
    # not itself evaluated.
    acceptable_sources: tuple[str, ...]

    offer_timing: OfferTiming
    is_mandatory: bool
    availability_kind: AvailabilityKind

    # Other feature_ids this one is computed from — only meaningful
    # when availability_kind == "derived"; empty for "direct" features.
    derivation_dependencies: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.availability_kind == "direct" and self.derivation_dependencies:
            raise ValueError(
                f"{self.feature_id}: a 'direct' feature must not declare derivation_dependencies"
            )
        if self.availability_kind == "derived" and not self.derivation_dependencies:
            raise ValueError(
                f"{self.feature_id}: a 'derived' feature must declare at least one derivation_dependencies entry"
            )
        if self.availability_kind == "derived" and self.required_source_fields:
            raise ValueError(
                f"{self.feature_id}: a 'derived' feature reads its dependencies' values, not "
                "required_source_fields directly — leave that tuple empty"
            )
