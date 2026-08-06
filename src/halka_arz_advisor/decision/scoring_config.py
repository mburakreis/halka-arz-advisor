"""Explicit, versioned scoring configuration for
:mod:`halka_arz_advisor.decision.engine` — every feature weight,
normalization threshold, category weight, confidence-component weight,
and decision threshold used by the engine lives here, in one place,
tagged with the config version that owns it. Nothing in the engine
hardcodes a number; it only reads a :class:`ScoringConfig` looked up by
version string (:data:`SCORING_CONFIGS`).

``expert_v0`` is a first, deliberately modest cut — an experienced
analyst's rough judgment calls turned into explicit numbers, not a
fitted/backtested model. Its normalization bands and feature weights
are documented inline with the reasoning behind each choice; a future
``expert_v1`` (or a data-fitted version) can override any of them
without touching engine code, since the engine only ever asks "what
does this version's config say" — see
:func:`halka_arz_advisor.decision.engine.evaluate_decision`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FeatureScoreKind = Literal["numeric", "presence"]


@dataclass(frozen=True, slots=True)
class NormalizationBand:
    """Piecewise-linear normalization: ``low_value`` maps to
    ``low_score``, ``high_value`` maps to ``high_score``, linearly
    interpolated between them and clamped outside that range. Whether
    a higher raw value is "better" is expressed by which of
    ``low_score``/``high_score`` is larger — no separate "direction"
    flag needed."""

    low_value: float
    low_score: float
    high_value: float
    high_score: float

    def normalize(self, value: float) -> float:
        if self.high_value == self.low_value:
            return self.high_score
        t = (value - self.low_value) / (self.high_value - self.low_value)
        t = max(0.0, min(1.0, t))
        return self.low_score + t * (self.high_score - self.low_score)


@dataclass(frozen=True, slots=True)
class FeatureScoringSpec:
    """One scored feature within a category — ``feature_id`` matches a
    :class:`~halka_arz_advisor.decision.models.FeatureSpec.feature_id`
    from the coverage catalog, so its value/status is read directly off
    that feature's already-computed :class:`~halka_arz_advisor.decision.audit.FeatureAuditResult`
    (no re-extraction, no new data source)."""

    feature_id: str
    weight: float
    kind: FeatureScoreKind
    # Required when kind == "numeric"; unused for "presence" (a
    # presence feature scores 100 when AVAILABLE/DERIVABLE, and is
    # simply excluded — not zeroed — when it isn't; see
    # halka_arz_advisor.decision.engine.score_category).
    normalization: NormalizationBand | None = None
    # For a feature whose *magnitude* of deviation matters more than
    # its sign (e.g. reported_pe_difference_percentage — a report's own
    # P/E being 20% above or 20% below our recalculation is equally
    # concerning), normalize abs(value) instead of value.
    use_absolute_value: bool = False

    def __post_init__(self) -> None:
        if self.kind == "numeric" and self.normalization is None:
            raise ValueError(f"{self.feature_id}: a 'numeric' scored feature needs a normalization band")


@dataclass(frozen=True, slots=True)
class CategoryScoringConfig:
    features: tuple[FeatureScoringSpec, ...]
    weight: float  # this category's share of total_score (fractions across the 3 categories sum to 1.0)
    coverage_threshold: float  # below this weighted-coverage fraction, the category is INSUFFICIENT

    def __post_init__(self) -> None:
        total = round(sum(f.weight for f in self.features), 6)
        if total != 100.0:
            raise ValueError(f"category feature weights must sum to 100, got {total}")


@dataclass(frozen=True, slots=True)
class ConfidenceComponentConfig:
    name: str
    weight: float  # fraction of data_confidence_score (all components sum to 1.0)


@dataclass(frozen=True, slots=True)
class DecisionThresholds:
    """Every number named in the brief, kept exactly as given —
    thresholds are on a 0-100 scale for scores/confidence."""

    participate_min_total: float = 68.0
    participate_min_confidence: float = 70.0
    participate_min_valuation: float = 55.0
    participate_min_fundamental_quality: float = 55.0

    limited_total_low: float = 50.0
    limited_total_high: float = 67.0
    limited_min_confidence_at_low_total: float = 55.0

    limited_min_total_at_high_confidence_band: float = 68.0
    limited_confidence_band_low: float = 45.0
    limited_confidence_band_high: float = 69.0

    skip_max_total: float = 50.0  # strictly below this
    skip_min_confidence: float = 55.0

    insufficient_max_confidence: float = 45.0  # strictly below this

    category_coverage_threshold: float = 0.60


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    version: str
    rule_version: str
    fundamental_quality: CategoryScoringConfig
    valuation: CategoryScoringConfig
    offering_structure: CategoryScoringConfig
    confidence_components: tuple[ConfidenceComponentConfig, ...]
    thresholds: DecisionThresholds
    # Days-since-most-recent-disclosure -> document_freshness component
    # score: 30 days or less -> 100, 365+ days -> 0, linear between.
    document_freshness_band: NormalizationBand = field(
        default_factory=lambda: NormalizationBand(low_value=30.0, low_score=100.0, high_value=365.0, high_score=0.0)
    )
    # Catalog feature_ids treated as "critical" for the unresolved-
    # critical-conflict hard rule and the critical-field-validation
    # confidence component — deliberately the same, small, mandatory
    # core every other part of this project already treats as central
    # (see e.g. decision.audit's own _CORE_FIELDS_FOR_CONFIDENCE).
    critical_field_ids: tuple[str, ...] = field(
        default_factory=lambda: (
            "offering_price",
            "distribution_method",
            "subscription_window",
            "total_offered_shares",
            "capital_increase_shares",
        )
    )

    def __post_init__(self) -> None:
        total = round(sum(c.weight for c in self.confidence_components), 6)
        if total != 1.0:
            raise ValueError(f"confidence component weights must sum to 1.0, got {total}")
        category_total = round(self.fundamental_quality.weight + self.valuation.weight + self.offering_structure.weight, 6)
        if category_total != 1.0:
            raise ValueError(f"category weights must sum to 1.0, got {category_total}")


# --------------------------------------------------------------------------
# expert_v0
# --------------------------------------------------------------------------
#
# Category feature sets are deliberately a *subset* of the full
# decision-feature catalog — the presence-only qualitative fields
# (business_description, key_risk_factors, use_of_proceeds_plan, ...)
# only ever score 100-or-excluded (see FeatureScoringSpec.kind), since
# there's no deterministic way to judge the *quality* of a risk
# disclosure's prose without an LLM (out of scope here — see the
# project's Gemini layer for narrative analysis instead). Only the
# derived financial ratios get real, direction-aware normalization
# bands; every band below is a first, explicit, documented judgment
# call — not a fitted or backtested threshold.

_FUNDAMENTAL_QUALITY_V0 = CategoryScoringConfig(
    weight=0.50,
    coverage_threshold=0.60,
    features=(
        FeatureScoringSpec("business_description", weight=10, kind="presence"),
        FeatureScoringSpec("key_risk_factors", weight=10, kind="presence"),
        FeatureScoringSpec("use_of_proceeds_plan", weight=10, kind="presence"),
        # -20% YoY revenue decline -> 0; +40% YoY growth -> 100.
        FeatureScoringSpec(
            "revenue_growth_yoy", weight=15, kind="numeric",
            normalization=NormalizationBand(low_value=-0.20, low_score=0.0, high_value=0.40, high_score=100.0),
        ),
        # -10% net margin (a loss) -> 0; 30%+ net margin -> 100.
        FeatureScoringSpec(
            "net_margin", weight=15, kind="numeric",
            normalization=NormalizationBand(low_value=-0.10, low_score=0.0, high_value=0.30, high_score=100.0),
        ),
        # Lower leverage is better: 0x debt/equity -> 100, 2x+ -> 0.
        FeatureScoringSpec(
            "debt_to_equity", weight=15, kind="numeric",
            normalization=NormalizationBand(low_value=0.0, low_score=100.0, high_value=2.0, high_score=0.0),
        ),
        # Current ratio: 0.5x (weak liquidity) -> 0, 2.5x -> 100.
        FeatureScoringSpec(
            "current_ratio", weight=10, kind="numeric",
            normalization=NormalizationBand(low_value=0.5, low_score=0.0, high_value=2.5, high_score=100.0),
        ),
        # Operating cash flow / net income: 0x (no cash conversion) -> 0,
        # 1.5x+ (strong conversion) -> 100.
        FeatureScoringSpec(
            "operating_cash_flow_to_net_income", weight=10, kind="numeric",
            normalization=NormalizationBand(low_value=0.0, low_score=0.0, high_value=1.5, high_score=100.0),
        ),
        # Interest coverage: 0x (can't cover financing expense) -> 0,
        # 10x+ -> 100.
        FeatureScoringSpec(
            "interest_coverage", weight=5, kind="numeric",
            normalization=NormalizationBand(low_value=0.0, low_score=0.0, high_value=10.0, high_score=100.0),
        ),
    ),
)

_VALUATION_V0 = CategoryScoringConfig(
    weight=0.30,
    coverage_threshold=0.60,
    features=(
        # Bigger discount to the report's own fair-value estimate ->
        # more margin of safety for a participant: 0% -> 0, 30%+ -> 100.
        FeatureScoringSpec(
            "headline_discount_percentage", weight=40, kind="numeric",
            normalization=NormalizationBand(low_value=0.0, low_score=0.0, high_value=30.0, high_score=100.0),
        ),
        # Reported P/E: cheaper is better. 5x or less -> 100, 30x+ -> 0.
        FeatureScoringSpec(
            "earnings_multiple_at_offer", weight=35, kind="numeric",
            normalization=NormalizationBand(low_value=5.0, low_score=100.0, high_value=30.0, high_score=0.0),
        ),
        # |recalculated P/E - reported P/E| / reported P/E, as a
        # percentage: 0% (perfectly consistent) -> 100, 50%+ deviation
        # in either direction -> 0.
        FeatureScoringSpec(
            "reported_pe_difference_percentage", weight=25, kind="numeric", use_absolute_value=True,
            normalization=NormalizationBand(low_value=0.0, low_score=100.0, high_value=50.0, high_score=0.0),
        ),
    ),
)

_OFFERING_STRUCTURE_V0 = CategoryScoringConfig(
    weight=0.20,
    coverage_threshold=0.60,
    features=(
        FeatureScoringSpec("subscription_window", weight=15, kind="presence"),
        FeatureScoringSpec("distribution_method", weight=15, kind="presence"),
        FeatureScoringSpec("total_offered_shares", weight=15, kind="presence"),
        FeatureScoringSpec("capital_increase_shares", weight=15, kind="presence"),
        # Higher capital-increase ratio -> more of the offering is new
        # growth capital for the company rather than existing holders
        # cashing out — a first, explicit, revisable judgment call, not
        # a universal truth: 0% -> 0, 100%+ -> 100.
        FeatureScoringSpec(
            "capital_increase_ratio", weight=40, kind="numeric",
            normalization=NormalizationBand(low_value=0.0, low_score=0.0, high_value=100.0, high_score=100.0),
        ),
    ),
)

_CONFIDENCE_COMPONENTS_V0 = (
    ConfidenceComponentConfig("required_document_completeness", weight=0.30),
    ConfidenceComponentConfig("critical_field_validation", weight=0.25),
    ConfidenceComponentConfig("extraction_quality", weight=0.15),
    ConfidenceComponentConfig("source_agreement", weight=0.15),
    ConfidenceComponentConfig("document_freshness", weight=0.15),
)

EXPERT_V0 = ScoringConfig(
    version="expert_v0",
    rule_version="expert_v0",
    fundamental_quality=_FUNDAMENTAL_QUALITY_V0,
    valuation=_VALUATION_V0,
    offering_structure=_OFFERING_STRUCTURE_V0,
    confidence_components=_CONFIDENCE_COMPONENTS_V0,
    thresholds=DecisionThresholds(),
)

SCORING_CONFIGS: dict[str, ScoringConfig] = {"expert_v0": EXPERT_V0}


def get_scoring_config(version: str = "expert_v0") -> ScoringConfig:
    try:
        return SCORING_CONFIGS[version]
    except KeyError:
        raise ValueError(f"unknown scoring config version {version!r}; known versions: {sorted(SCORING_CONFIGS)}") from None
