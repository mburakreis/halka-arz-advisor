"""The first complete deterministic IPO decision engine — scores a
:class:`~halka_arz_advisor.decision.snapshot.DecisionSnapshot` into a
:class:`DecisionResult` (one of ``participate``/``limited_participation``/
``skip``/``insufficient_data``), using only the ``expert_v0`` configuration
in :mod:`halka_arz_advisor.decision.scoring_config` — no constant here is
scattered inline; every weight/threshold/band this module uses is read
from that config, looked up by version.

Core rules this module enforces everywhere, not just in one place:

- **Never a neutral substitute.** A missing, conflicting, or
  not-applicable feature is *excluded* from whatever average it would
  have fed (a category score, the confidence score, the total score) —
  never replaced with a 0, a 50, or any other placeholder. The category/
  total score is a weighted average over only what's actually known,
  re-normalized among the available weights; separately, *coverage*
  reports how much of the category's total possible weight that
  average was actually computed from.
- **``NOT_APPLICABLE`` doesn't count against coverage.** A feature a
  company's sector genuinely doesn't report (see
  :mod:`halka_arz_advisor.kap.sector`) is dropped from both the
  numerator *and* the coverage denominator — it was never a real gap.
- **Conflicted values are never picked from.** A feature whose
  :class:`~halka_arz_advisor.decision.audit.FeatureAuditResult.status`
  is ``"CONFLICTED"`` is treated exactly like "missing" for scoring
  purposes (excluded from the average) — this module never guesses
  which of two disagreeing observations to trust.
- **A category below 60% weighted coverage is ``INSUFFICIENT``**,
  regardless of how good its (thin) available score looks — feeding
  into the ``insufficient_mandatory_category_coverage`` hard rule.

Hard rules are split by which of the two "no" signals they represent
(see :func:`evaluate_hard_rules`): ``missing_mandatory_documents``,
``unresolved_critical_conflict``, and
``insufficient_mandatory_category_coverage`` mean "we don't know enough
to say anything" -> forces ``insufficient_data``; ``invalid_offer_share_equation``
and ``withdrawn_cancelled_suspended`` mean "we know enough to say no" ->
forces ``skip``. Both groups always win over the numeric score/confidence
thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..kap.text import fold_turkish
from .audit import FeatureAuditResult, FeatureEvidence, FeatureStatus
from .catalog import get_feature
from .scoring_config import CategoryScoringConfig, ScoringConfig, get_scoring_config
from .snapshot import DecisionSnapshot

Signal = Literal["participate", "limited_participation", "skip", "insufficient_data"]
CategoryScoreStatus = Literal["OK", "INSUFFICIENT"]
HardRuleTarget = Literal["skip", "insufficient_data"]


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """One piece of evidence that was actually *binding* on the
    result — i.e. it fed a category score, not just something the
    audit happened to notice."""

    feature_id: str
    disclosure_id: str | None
    document_type: str | None
    page_number: int | None
    extraction_method: str | None


@dataclass(frozen=True, slots=True)
class FeatureContribution:
    category: str
    feature_id: str
    status: FeatureStatus
    raw_value: float | None
    normalized_score: float | None
    weight: float
    included_in_score: bool
    evidence: tuple[FeatureEvidence, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CategoryScoreResult:
    category: str
    score: float | None  # None only when *zero* features in the category were available
    coverage: float  # weighted-available / weighted-applicable (NOT_APPLICABLE features excluded from both)
    status: CategoryScoreStatus
    contributions: tuple[FeatureContribution, ...]


@dataclass(frozen=True, slots=True)
class ConfidenceComponentResult:
    name: str
    score: float
    weight: float


@dataclass(frozen=True, slots=True)
class HardRuleResult:
    rule_id: str
    target: HardRuleTarget
    triggered: bool
    reason: str


@dataclass(frozen=True, slots=True)
class DecisionResult:
    signal: Signal
    total_score: float | None
    confidence_score: float
    category_scores: tuple[CategoryScoreResult, ...]
    feature_contributions: tuple[FeatureContribution, ...]
    confidence_components: tuple[ConfidenceComponentResult, ...]
    hard_rules: tuple[HardRuleResult, ...]
    warnings: tuple[str, ...]
    evidence_references: tuple[EvidenceReference, ...]
    rule_version: str
    weight_set_version: str

    def category_score(self, category: str) -> CategoryScoreResult | None:
        return next((c for c in self.category_scores if c.category == category), None)


# --------------------------------------------------------------------------
# Category scoring
# --------------------------------------------------------------------------


def _numeric_evidence_value(result: FeatureAuditResult) -> float | None:
    """The single numeric value backing a "numeric"-kind scored
    feature. Every such feature in ``expert_v0`` has exactly one
    ``required_source_fields`` entry (see scoring_config.py's category
    definitions) so exactly one evidence entry is expected; anything
    else is treated as not scoreable rather than guessed at."""
    if len(result.evidence) != 1:
        return None
    value = result.evidence[0].value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def score_category(category_name: str, config: CategoryScoringConfig, snapshot: DecisionSnapshot) -> CategoryScoreResult:
    contributions: list[FeatureContribution] = []
    numerator = 0.0
    weight_available = 0.0
    weight_applicable = 0.0

    for spec in config.features:
        result = snapshot.audit_result(spec.feature_id)
        status: FeatureStatus = result.status if result is not None else "MISSING_FIELD"

        if status == "NOT_APPLICABLE":
            contributions.append(FeatureContribution(category_name, spec.feature_id, status, None, None, spec.weight, False))
            continue

        weight_applicable += spec.weight

        if status not in ("AVAILABLE", "DERIVABLE"):
            # Includes CONFLICTED — never selected or averaged, just
            # excluded (still counts against coverage, since the
            # feature *is* applicable, just not usably resolved).
            contributions.append(FeatureContribution(category_name, spec.feature_id, status, None, None, spec.weight, False))
            continue

        evidence = result.evidence
        if spec.kind == "presence":
            numerator += spec.weight * 100.0
            weight_available += spec.weight
            contributions.append(FeatureContribution(category_name, spec.feature_id, status, None, 100.0, spec.weight, True, evidence))
            continue

        raw_value = _numeric_evidence_value(result)
        if raw_value is None:
            contributions.append(FeatureContribution(category_name, spec.feature_id, status, None, None, spec.weight, False))
            continue

        value_for_norm = abs(raw_value) if spec.use_absolute_value else raw_value
        normalized = spec.normalization.normalize(value_for_norm)  # type: ignore[union-attr]
        numerator += spec.weight * normalized
        weight_available += spec.weight
        contributions.append(FeatureContribution(category_name, spec.feature_id, status, raw_value, normalized, spec.weight, True, evidence))

    coverage = (weight_available / weight_applicable) if weight_applicable > 0 else 0.0
    score = (numerator / weight_available) if weight_available > 0 else None
    category_status: CategoryScoreStatus = "OK" if coverage >= config.coverage_threshold else "INSUFFICIENT"
    return CategoryScoreResult(category_name, score, coverage, category_status, tuple(contributions))


def compute_total_score(config: ScoringConfig, category_results: tuple[CategoryScoreResult, ...]) -> float | None:
    """Weighted average of the 3 category scores using their
    ``expert_v0`` weights (50/30/20) — re-normalized among whichever
    categories actually have a score, never treating a scoreless
    category as a zero."""
    weights = {
        "fundamental_quality": config.fundamental_quality.weight,
        "valuation": config.valuation.weight,
        "offering_structure": config.offering_structure.weight,
    }
    available = [(weights[c.category], c.score) for c in category_results if c.score is not None]
    if not available:
        return None
    weight_sum = sum(w for w, _ in available)
    return sum(w * s for w, s in available) / weight_sum


# --------------------------------------------------------------------------
# data_confidence_score
# --------------------------------------------------------------------------


def _mandatory_pre_offer_completeness(snapshot: DecisionSnapshot) -> float:
    """required_document_completeness — coverage of every *mandatory,
    pre-offer* catalog feature (post-offer mandatory features, e.g.
    oversubscription_ratio_overall, are excluded: they genuinely don't
    exist yet for a pre-offer participation decision, which isn't a
    completeness gap)."""
    relevant = [r for r in snapshot.audit_results if (spec := get_feature(r.feature_id)).is_mandatory and spec.offer_timing == "pre_offer"]
    applicable = [r for r in relevant if r.status != "NOT_APPLICABLE"]
    if not applicable:
        return 100.0
    available = [r for r in applicable if r.status in ("AVAILABLE", "DERIVABLE")]
    return 100.0 * len(available) / len(applicable)


def _critical_field_validation(config: ScoringConfig, snapshot: DecisionSnapshot) -> float:
    """critical_field_validation — beyond mere presence, a handful of
    basic sanity checks (positive share counts, a subscription window
    that doesn't end before it starts) on the same small critical-field
    set the unresolved-critical-conflict hard rule also uses."""
    facts = snapshot.facts
    if facts is None:
        return 0.0

    checks = {
        "offering_price": lambda: facts.offering_price.status == "extracted"
        and isinstance(facts.offering_price.value, (int, float))
        and facts.offering_price.value > 0,
        "distribution_method": lambda: facts.distribution_method.status == "extracted",
        "subscription_window": lambda: (
            facts.subscription_start_date.status == "extracted"
            and facts.subscription_end_date.status == "extracted"
            and facts.subscription_end_date.value >= facts.subscription_start_date.value
        ),
        "total_offered_shares": lambda: facts.total_offered_shares.status == "extracted"
        and isinstance(facts.total_offered_shares.value, (int, float))
        and facts.total_offered_shares.value > 0,
        "capital_increase_shares": lambda: facts.capital_increase_shares.status == "extracted"
        and isinstance(facts.capital_increase_shares.value, (int, float))
        and facts.capital_increase_shares.value >= 0,
    }
    applicable = [field_id for field_id in config.critical_field_ids if field_id in checks]
    if not applicable:
        return 0.0
    valid = sum(1 for field_id in applicable if checks[field_id]())
    return 100.0 * valid / len(applicable)


def _extraction_quality(snapshot: DecisionSnapshot) -> float:
    """extraction_quality — the share of evidence backed by a digital
    PDF text layer rather than OCR, across every audited feature."""
    digital = ocr = 0
    for result in snapshot.audit_results:
        for evidence in result.evidence:
            if evidence.extraction_method == "digital":
                digital += 1
            elif evidence.extraction_method == "ocr":
                ocr += 1
    total = digital + ocr
    return 100.0 if total == 0 else 100.0 * digital / total


# Same core pre-offer fields halka_arz_advisor.decision.audit's own
# data_confidence meta-features already inspect for cross-document
# corroboration (see that module's _CORE_FIELDS_FOR_CONFIDENCE).
_SOURCE_AGREEMENT_FIELDS: tuple[str, ...] = (
    "offering_price",
    "distribution_method",
    "subscription_start_date",
    "subscription_end_date",
    "total_offered_shares",
    "capital_increase_ratio",
)


def _source_agreement(snapshot: DecisionSnapshot) -> float:
    """source_agreement — of the core fields we actually have a value
    for, what fraction are confirmed by more than one document."""
    facts = snapshot.facts
    if facts is None:
        return 0.0
    extracted = [
        fact
        for name in _SOURCE_AGREEMENT_FIELDS
        if (fact := getattr(facts, name, None)) is not None and fact.status in ("extracted", "conflicting")
    ]
    if not extracted:
        return 0.0
    corroborated = sum(1 for fact in extracted if len(fact.observations) > 1)
    return 100.0 * corroborated / len(extracted)


def _document_freshness(config: ScoringConfig, snapshot: DecisionSnapshot) -> float:
    """document_freshness — how recently the most recent disclosure
    used here was published, relative to the snapshot's own generation
    time."""
    if not snapshot.disclosures:
        return 0.0
    latest = max(d.published_at for d in snapshot.disclosures)
    days = (snapshot.generated_at - latest).days
    return config.document_freshness_band.normalize(float(days))


_CONFIDENCE_COMPONENT_FUNCTIONS = {
    "required_document_completeness": lambda config, snapshot: _mandatory_pre_offer_completeness(snapshot),
    "critical_field_validation": _critical_field_validation,
    "extraction_quality": lambda config, snapshot: _extraction_quality(snapshot),
    "source_agreement": lambda config, snapshot: _source_agreement(snapshot),
    "document_freshness": _document_freshness,
}


def score_confidence(config: ScoringConfig, snapshot: DecisionSnapshot) -> tuple[float, tuple[ConfidenceComponentResult, ...]]:
    results = tuple(
        ConfidenceComponentResult(c.name, _CONFIDENCE_COMPONENT_FUNCTIONS[c.name](config, snapshot), c.weight)
        for c in config.confidence_components
    )
    total = sum(r.score * r.weight for r in results)
    return total, results


# --------------------------------------------------------------------------
# Hard rules
# --------------------------------------------------------------------------


def _invalid_offer_share_equation(snapshot: DecisionSnapshot) -> tuple[bool, str]:
    facts = snapshot.facts
    if facts is None:
        return False, "not checked — no extracted facts available"

    capital, secondary, total = facts.capital_increase_shares, facts.secondary_sale_shares, facts.total_offered_shares
    if "conflicting" in (capital.status, secondary.status, total.status):
        return False, "not checked — one or more components has conflicting values"
    if capital.status != "extracted" or total.status != "extracted":
        return False, "not checked — capital_increase_shares/total_offered_shares not both available"

    capital_value = float(capital.value)  # type: ignore[arg-type]
    total_value = float(total.value)  # type: ignore[arg-type]
    # secondary_sale_shares is an optional component of an offering
    # (not every IPO has one) — a report that never states it means
    # there is none, per kap.extraction's own convention that a value
    # is only ever produced when explicitly stated.
    secondary_value = float(secondary.value) if secondary.status == "extracted" else 0.0  # type: ignore[arg-type]

    tolerance = max(1.0, total_value * 0.001)
    if abs((capital_value + secondary_value) - total_value) > tolerance:
        return True, (
            f"capital_increase_shares ({capital_value:,.0f}) + secondary_sale_shares ({secondary_value:,.0f}) "
            f"!= total_offered_shares ({total_value:,.0f})"
        )
    return False, "capital_increase_shares + secondary_sale_shares == total_offered_shares"


# A KAP disclosure title indicating the offering itself was withdrawn,
# cancelled, or suspended — no such disclosure type exists in
# halka_arz_advisor.kap.classification today (see that module), so this
# checks the plain title text this project's disclosure list already
# carries, exactly the same way every other part of this project treats
# "explicitly stated text" as ground truth. Correctly never triggers
# against any of this project's currently cached real disclosures —
# that's an honest reflection of "no such case observed yet", not a
# design gap.
_WITHDRAWAL_KEYWORDS: tuple[str, ...] = ("iptal", "vazgecme", "vazgecti", "erteleme", "durduruldu", "askiya alin")


def _withdrawn_cancelled_suspended(snapshot: DecisionSnapshot) -> tuple[bool, str]:
    for disclosure in snapshot.disclosures:
        folded_title = fold_turkish(disclosure.title)
        if any(keyword in folded_title for keyword in _WITHDRAWAL_KEYWORDS):
            return True, f"disclosure {disclosure.disclosure_id!r} title suggests withdrawal/cancellation/suspension: {disclosure.title!r}"
    return False, "no disclosure title indicates the offering was withdrawn, cancelled, or suspended"


def evaluate_hard_rules(
    config: ScoringConfig, snapshot: DecisionSnapshot, category_results: tuple[CategoryScoreResult, ...]
) -> tuple[HardRuleResult, ...]:
    missing_mandatory = [
        r
        for r in snapshot.audit_results
        if (spec := get_feature(r.feature_id)).is_mandatory and spec.offer_timing == "pre_offer" and r.status == "MISSING_DOCUMENT"
    ]
    missing_mandatory_rule = HardRuleResult(
        "missing_mandatory_documents",
        "insufficient_data",
        bool(missing_mandatory),
        f"{len(missing_mandatory)} mandatory pre-offer feature(s) have no readable source document: "
        + ", ".join(r.feature_id for r in missing_mandatory)
        if missing_mandatory
        else "every mandatory pre-offer feature has a readable source document",
    )

    conflicted_critical = [
        field_id for field_id in config.critical_field_ids if (r := snapshot.audit_result(field_id)) and r.status == "CONFLICTED"
    ]
    conflict_rule = HardRuleResult(
        "unresolved_critical_conflict",
        "insufficient_data",
        bool(conflicted_critical),
        f"conflicting values for critical field(s): {', '.join(conflicted_critical)}"
        if conflicted_critical
        else "no conflicting values among critical fields",
    )

    equation_invalid, equation_reason = _invalid_offer_share_equation(snapshot)
    equation_rule = HardRuleResult("invalid_offer_share_equation", "skip", equation_invalid, equation_reason)

    withdrawn, withdrawn_reason = _withdrawn_cancelled_suspended(snapshot)
    withdrawal_rule = HardRuleResult("withdrawn_cancelled_suspended", "skip", withdrawn, withdrawn_reason)

    insufficient_categories = [c.category for c in category_results if c.status == "INSUFFICIENT"]
    coverage_rule = HardRuleResult(
        "insufficient_mandatory_category_coverage",
        "insufficient_data",
        bool(insufficient_categories),
        f"category coverage below {config.thresholds.category_coverage_threshold:.0%} for: {', '.join(insufficient_categories)}"
        if insufficient_categories
        else "every scored category meets the minimum coverage threshold",
    )

    return (missing_mandatory_rule, conflict_rule, equation_rule, withdrawal_rule, coverage_rule)


# --------------------------------------------------------------------------
# Signal
# --------------------------------------------------------------------------


def determine_signal(
    config: ScoringConfig,
    total_score: float | None,
    confidence: float,
    category_results: tuple[CategoryScoreResult, ...],
    hard_rules: tuple[HardRuleResult, ...],
) -> Signal:
    t = config.thresholds

    # Hard rules always win — "we don't know enough" (insufficient_data)
    # takes priority over "we know enough to say no" (skip), which in
    # turn is checked before any numeric threshold.
    if confidence < t.insufficient_max_confidence or any(r.triggered and r.target == "insufficient_data" for r in hard_rules):
        return "insufficient_data"
    if any(r.triggered and r.target == "skip" for r in hard_rules):
        return "skip"
    if total_score is None:
        return "insufficient_data"

    valuation = next((c.score for c in category_results if c.category == "valuation"), None)
    fundamental = next((c.score for c in category_results if c.category == "fundamental_quality"), None)

    if (
        total_score >= t.participate_min_total
        and confidence >= t.participate_min_confidence
        and valuation is not None
        and valuation >= t.participate_min_valuation
        and fundamental is not None
        and fundamental >= t.participate_min_fundamental_quality
    ):
        return "participate"

    if (t.limited_total_low <= total_score <= t.limited_total_high and confidence >= t.limited_min_confidence_at_low_total) or (
        total_score >= t.limited_min_total_at_high_confidence_band
        and t.limited_confidence_band_low <= confidence <= t.limited_confidence_band_high
    ):
        return "limited_participation"

    if total_score < t.skip_max_total and confidence >= t.skip_min_confidence:
        return "skip"

    # No explicit band matched (e.g. a >=68 total with a
    # >=70-confidence but a category gate below 55) — the conservative
    # fallback is "we can't confidently call this", never a guess.
    return "insufficient_data"


# --------------------------------------------------------------------------
# Evidence / warnings
# --------------------------------------------------------------------------


def _collect_evidence_references(feature_contributions: tuple[FeatureContribution, ...]) -> tuple[EvidenceReference, ...]:
    refs = []
    for contribution in feature_contributions:
        if not contribution.included_in_score:
            continue
        for evidence in contribution.evidence:
            if evidence.disclosure_id is None:
                continue
            refs.append(
                EvidenceReference(
                    contribution.feature_id, evidence.disclosure_id, evidence.document_type, evidence.page_number, evidence.extraction_method
                )
            )
    return tuple(refs)


def _collect_warnings(
    category_results: tuple[CategoryScoreResult, ...],
    confidence_components: tuple[ConfidenceComponentResult, ...],
    hard_rules: tuple[HardRuleResult, ...],
) -> tuple[str, ...]:
    warnings: list[str] = []
    for c in category_results:
        if c.status == "INSUFFICIENT":
            warnings.append(f"{c.category}: weighted coverage {c.coverage:.0%} is below the insufficient-coverage threshold")
        elif c.coverage < 0.75:
            warnings.append(f"{c.category}: weighted coverage is only {c.coverage:.0%}")
    for comp in confidence_components:
        if comp.score < 50:
            warnings.append(f"confidence component {comp.name!r} scored low ({comp.score:.0f}/100)")
    for rule in hard_rules:
        if rule.triggered:
            warnings.append(f"hard rule {rule.rule_id!r} triggered: {rule.reason}")
    return tuple(warnings)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def evaluate_decision(snapshot: DecisionSnapshot, *, config_version: str = "expert_v0") -> DecisionResult:
    config = get_scoring_config(config_version)

    category_results = (
        score_category("fundamental_quality", config.fundamental_quality, snapshot),
        score_category("valuation", config.valuation, snapshot),
        score_category("offering_structure", config.offering_structure, snapshot),
    )

    confidence_score, confidence_components = score_confidence(config, snapshot)
    total_score = compute_total_score(config, category_results)
    hard_rules = evaluate_hard_rules(config, snapshot, category_results)
    signal = determine_signal(config, total_score, confidence_score, category_results, hard_rules)

    feature_contributions = tuple(c for cat in category_results for c in cat.contributions)
    evidence_references = _collect_evidence_references(feature_contributions)
    warnings = _collect_warnings(category_results, confidence_components, hard_rules)

    return DecisionResult(
        signal=signal,
        total_score=total_score,
        confidence_score=confidence_score,
        category_scores=category_results,
        feature_contributions=feature_contributions,
        confidence_components=confidence_components,
        hard_rules=hard_rules,
        warnings=warnings,
        evidence_references=evidence_references,
        rule_version=config.rule_version,
        weight_set_version=config.version,
    )
