"""``SubscriptionDecisionV1`` — a deterministic, non-compensatory
pre-offer *subscription* decision, deliberately separate from
``expert_v0`` (:mod:`halka_arz_advisor.decision.engine`/
:mod:`halka_arz_advisor.decision.scoring_config`): no weighted average,
no global 0-100 score, no category-coverage machinery, and no import
from ``engine``/``scoring_config``/``catalog``/``audit``/``snapshot``/
``pipeline`` — this module reads only
:mod:`halka_arz_advisor.kap.offering_terms`,
:mod:`halka_arz_advisor.kap.manual_confirmation`,
:mod:`halka_arz_advisor.kap.allocation_scenario`,
:mod:`halka_arz_advisor.kap.derived_financials`, and
:mod:`halka_arz_advisor.evds.models`.

**Gates, not points.** Every rule below is a pass/fail gate evaluated in
a fixed order; nothing here sums or averages a "score" across rules, so
a strong result on one axis can never buy back a failure on another:

- **Critical subscription evidence is a gate.** If any of ``offer_price``,
  ``subscription_start``, ``subscription_end``, ``distribution_method``,
  ``total_offered_shares``, ``retail_distribution_rule``, or at least
  one of ``retail_offered_shares``/``retail_allocation_percentage`` is
  not resolved (automatically *or* via a manual
  :mod:`~halka_arz_advisor.kap.manual_confirmation` — see
  :data:`_CRITICAL_FIELDS`/:data:`_RETAIL_EVIDENCE_FIELDS`), the result
  is ``CANNOT_ASSESS_SUBSCRIPTION`` — never a guess, and never the same
  as "we looked and it's bad" (``PASS_SUBSCRIPTION``).
- **A structural red flag (an internally inconsistent offered-share
  equation, or a disclosure title indicating withdrawal/cancellation/
  suspension) forces ``PASS_SUBSCRIPTION`` outright** — no amount of
  positive company evidence overrides it (see
  :func:`_evaluate_red_flags`).
- **``subscription_edge`` (mechanics favorability) is computed purely
  from ``OfferingTerms``** — never from a discount/valuation comparison
  (a headline discount alone is deliberately never treated as a
  positive signal here) and never from macro/BIST data. An
  ``"UNFAVORABLE"`` edge blocks ``SUBSCRIBE_*`` regardless of how good
  the company's fundamentals look.
- **``ownership_view`` is computed purely from
  :class:`~halka_arz_advisor.kap.derived_financials.DerivedFinancialFeatures`**
  — never from market-context/BIST-regime data (so a strong BIST regime
  can never compensate for weak company evidence) and never from
  ``ipo_outcomes``/historical labels (no outcome leakage, no
  outcome-tuned thresholds; every threshold here is a stated, reasoned
  constant, not fit to past returns). A single red-flag ratio (negative
  margin, high leverage, sub-1.0 liquidity/coverage) forces
  ``AVOID_LONG_TERM`` outright, the same non-compensable pattern as the
  subscription red-flag gate.
- ``ownership_view`` can be ``NOT_ASSESSABLE`` while the *subscription*
  action is still ``SUBSCRIBE_FOR_LISTING_TRADE`` — a pure listing-day
  flip doesn't require any view on the company's fundamentals, only on
  the mechanics of getting (and immediately selling) shares.
- :data:`~halka_arz_advisor.evds.models.MarketContextSnapshot` is
  carried on the inputs and surfaced by callers (e.g. the Telegram
  card) purely as *regime context* — it is never read by any function
  in this module that decides ``action``/``subscription_edge``/
  ``ownership_view``. ``policy_rate_minus_cpi`` in particular must never
  drive this decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..evds.models import MarketContextSnapshot
from ..kap.allocation_scenario import AllocationScenario, build_allocation_scenario
from ..kap.derived_financials import DERIVED_FINANCIAL_FEATURE_NAMES, DerivedFinancialFeatures
from ..kap.manual_confirmation import CompletedOfferingTerms, effective_offering_terms
from ..kap.models import KapDisclosure
from ..kap.offering_terms import OfferingTerms
from ..kap.text import fold_turkish

RULE_VERSION = "subscription_v1_r1"

SubscriptionAction = Literal[
    "SUBSCRIBE_FOR_LISTING_TRADE",
    "SUBSCRIBE_WITH_HOLD_OPTION",
    "PASS_SUBSCRIPTION",
    "PASS_AND_REASSESS_AFTER_LISTING",
    "CANNOT_ASSESS_SUBSCRIPTION",
]
SubscriptionEdge = Literal["FAVORABLE", "NEUTRAL", "UNFAVORABLE", "UNKNOWN"]
OwnershipView = Literal["HOLD_CANDIDATE", "WATCH", "AVOID_LONG_TERM", "NOT_ASSESSABLE"]
EvidenceGrade = Literal["STRONG", "MODERATE", "WEAK", "NONE"]
SubscriptionHorizon = Literal["listing_day_flip", "flip_or_hold", "watch_post_listing", "not_applicable"]

# The non-retail critical fields — every one must be effective_status
# == "extracted" (automatic or manually confirmed) or the result is
# CANNOT_ASSESS_SUBSCRIPTION.
_CRITICAL_FIELDS: tuple[str, ...] = (
    "offer_price",
    "subscription_start",
    "subscription_end",
    "distribution_method",
    "total_offered_shares",
    "retail_distribution_rule",
)
# Either one of these being resolved satisfies the "retail tranche size
# is known" half of the critical-evidence gate — a document can state
# one without the other (see kap.offering_terms), and either is enough
# to reason about the retail tranche.
_RETAIL_EVIDENCE_FIELDS: tuple[str, ...] = ("retail_offered_shares", "retail_allocation_percentage")

# Reasoned, stated constants — never fit against historical outcomes.
# A retail tranche below 10% of a Turkish IPO's total offering is thin
# relative to the SPK-mandated minimums this project has observed in
# real tahsisat tables (see kap.extraction); 20%+ is a substantial,
# guaranteed-access tranche under equal distribution.
_RETAIL_ALLOCATION_THIN_PCT = 10.0
_RETAIL_ALLOCATION_SUBSTANTIAL_PCT = 20.0

# Ownership-view ratio gates — plain, commonly used solvency/liquidity
# thresholds (debt/equity > 3x, current ratio < 1.0, interest coverage
# < 1.0x are all standard "this needs a closer look" lines in equity
# analysis), never tuned against this project's own ipo_outcomes data.
_DEBT_TO_EQUITY_RED_FLAG = 3.0
_CURRENT_RATIO_RED_FLAG = 1.0
_INTEREST_COVERAGE_RED_FLAG = 1.0
_CURRENT_RATIO_POSITIVE = 1.5
_DEBT_TO_EQUITY_POSITIVE = 1.0
_INTEREST_COVERAGE_POSITIVE = 3.0
_MIN_RESOLVED_RATIOS_FOR_HOLD_CANDIDATE = 2

# A handful of round, illustrative hypothetical retail-participant
# counts spanning a small/medium/large demand scenario — never a
# forecast of actual demand (see kap.allocation_scenario's own module
# docstring: the count is always a caller-supplied what-if).
DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS: tuple[int, ...] = (50_000, 200_000, 500_000)

# Same generic Turkish withdrawal/cancellation/postponement keyword set
# halka_arz_advisor.decision.engine's own hard rule uses — reimplemented
# here (not imported) to keep this module fully independent of
# expert_v0's engine module, per this module's own docstring.
_WITHDRAWAL_KEYWORDS: tuple[str, ...] = ("iptal", "vazgecme", "vazgecti", "erteleme", "durduruldu", "askiya alin")

_HORIZON_BY_ACTION: dict[SubscriptionAction, SubscriptionHorizon] = {
    "SUBSCRIBE_FOR_LISTING_TRADE": "listing_day_flip",
    "SUBSCRIBE_WITH_HOLD_OPTION": "flip_or_hold",
    "PASS_SUBSCRIPTION": "not_applicable",
    "PASS_AND_REASSESS_AFTER_LISTING": "watch_post_listing",
    "CANNOT_ASSESS_SUBSCRIPTION": "not_applicable",
}


@dataclass(frozen=True, slots=True)
class SubscriptionDecisionInputs:
    offering_terms: OfferingTerms
    completed_terms: CompletedOfferingTerms
    derived_financials: DerivedFinancialFeatures | None
    market_context: MarketContextSnapshot | None
    as_of: datetime
    disclosures: tuple[KapDisclosure, ...] = ()


@dataclass(frozen=True, slots=True)
class SubscriptionDecisionV1:
    action: SubscriptionAction
    subscription_edge: SubscriptionEdge
    intended_horizon: SubscriptionHorizon
    evidence_grade: EvidenceGrade
    ownership_view: OwnershipView
    allocation_scenarios: tuple[AllocationScenario, ...]
    strongest_positive_evidence: tuple[str, ...]
    strongest_risks: tuple[str, ...]
    missing_critical_evidence: tuple[str, ...]
    reasons: tuple[str, ...]
    rule_version: str


def _missing_critical_evidence(completed: CompletedOfferingTerms) -> tuple[str, ...]:
    missing: list[str] = []
    for name in _CRITICAL_FIELDS:
        status = completed.get(name).effective_status
        if status == "not_found":
            missing.append(name)
        elif status == "conflicting":
            missing.append(f"{name} (conflicting)")

    retail_statuses = [completed.get(name).effective_status for name in _RETAIL_EVIDENCE_FIELDS]
    if not any(s == "extracted" for s in retail_statuses):
        label = "retail_offered_shares_or_retail_allocation_percentage"
        missing.append(f"{label} (conflicting)" if any(s == "conflicting" for s in retail_statuses) else label)
    return tuple(missing)


def _evaluate_red_flags(
    offering_terms: OfferingTerms, completed: CompletedOfferingTerms, disclosures: tuple[KapDisclosure, ...]
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []

    new_issue = offering_terms.new_issue_shares
    total_field = completed.get("total_offered_shares")
    if new_issue.status == "extracted" and total_field.effective_status == "extracted":
        secondary = offering_terms.secondary_sale_shares
        secondary_value = float(secondary.value) if secondary.status == "extracted" else 0.0
        total_value = float(total_field.effective_value)  # type: ignore[arg-type]
        tolerance = max(1.0, total_value * 0.001)
        computed_total = float(new_issue.value) + secondary_value  # type: ignore[arg-type]
        if abs(computed_total - total_value) > tolerance:
            reasons.append(
                f"new_issue_shares ({new_issue.value:,.0f}) + secondary_sale_shares ({secondary_value:,.0f}) "
                f"!= total_offered_shares ({total_value:,.0f})"
            )

    for disclosure in disclosures:
        folded_title = fold_turkish(disclosure.title)
        if any(keyword in folded_title for keyword in _WITHDRAWAL_KEYWORDS):
            reasons.append(f"disclosure title suggests withdrawal/cancellation/suspension: {disclosure.title!r}")
            break

    return bool(reasons), tuple(reasons)


def _subscription_edge(completed: CompletedOfferingTerms, red_flag_triggered: bool) -> tuple[SubscriptionEdge, tuple[str, ...]]:
    if red_flag_triggered:
        return "UNFAVORABLE", ("a structural red flag was found in the offering's own stated mechanics (see risks)",)

    rule_field = completed.get("retail_distribution_rule")
    if rule_field.effective_status != "extracted":
        return "UNKNOWN", ("retail_distribution_rule is not resolved (automatically or manually)",)

    if rule_field.effective_value == "proportional":
        return "UNFAVORABLE", (
            "retail tranche uses proportional distribution — the actual per-investor allocation depends on "
            "total demand at subscription close and cannot be sized in advance",
        )

    pct_field = completed.get("retail_allocation_percentage")
    if pct_field.effective_status != "extracted":
        return "NEUTRAL", ("retail distribution is equal, but the retail tranche's size (% of the offering) is not known",)

    pct = float(pct_field.effective_value)  # type: ignore[arg-type]
    if pct < _RETAIL_ALLOCATION_THIN_PCT:
        return "UNFAVORABLE", (f"retail tranche is thin: only {pct:.1f}% of the offering is allocated to retail investors",)
    if pct >= _RETAIL_ALLOCATION_SUBSTANTIAL_PCT:
        return "FAVORABLE", (f"retail distribution is equal with a substantial {pct:.1f}% retail tranche",)
    return "NEUTRAL", (f"retail distribution is equal with a {pct:.1f}% retail tranche (neither thin nor substantial)",)


def _ownership_view(derived: DerivedFinancialFeatures | None) -> tuple[OwnershipView, tuple[str, ...], tuple[str, ...]]:
    """Never reads market_context — see module docstring."""
    if derived is None:
        return "NOT_ASSESSABLE", (), ()

    def resolved_value(name: str) -> float | None:
        feature = getattr(derived, name)
        return feature.value if feature.status == "computed" and feature.value is not None else None

    net_margin = resolved_value("net_margin")
    debt_to_equity = resolved_value("debt_to_equity")
    current_ratio = resolved_value("current_ratio")
    interest_coverage = resolved_value("interest_coverage")

    risks: list[str] = []
    if net_margin is not None and net_margin < 0:
        risks.append(f"net_margin is negative ({net_margin:.1f}%) — the company reported a net loss")
    if debt_to_equity is not None and debt_to_equity > _DEBT_TO_EQUITY_RED_FLAG:
        risks.append(f"debt_to_equity is high ({debt_to_equity:.2f})")
    if current_ratio is not None and current_ratio < _CURRENT_RATIO_RED_FLAG:
        risks.append(f"current_ratio is below 1.0 ({current_ratio:.2f}) — potential liquidity risk")
    if interest_coverage is not None and interest_coverage < _INTEREST_COVERAGE_RED_FLAG:
        risks.append(f"interest_coverage is below 1.0 ({interest_coverage:.2f}) — operating profit may not cover finance expense")
    if risks:
        return "AVOID_LONG_TERM", (), tuple(risks)

    candidate_values: dict[str, float | None] = {
        "revenue_growth_yoy": resolved_value("revenue_growth_yoy"),
        "net_margin": net_margin,
        "current_ratio": current_ratio,
        "debt_to_equity": debt_to_equity,
        "interest_coverage": interest_coverage,
    }
    positive_predicates: dict[str, object] = {
        "revenue_growth_yoy": lambda v: v > 0,
        "net_margin": lambda v: v > 0,
        "current_ratio": lambda v: v >= _CURRENT_RATIO_POSITIVE,
        "debt_to_equity": lambda v: v <= _DEBT_TO_EQUITY_POSITIVE,
        "interest_coverage": lambda v: v >= _INTEREST_COVERAGE_POSITIVE,
    }
    resolved = {name: value for name, value in candidate_values.items() if value is not None}
    if not resolved:
        return "NOT_ASSESSABLE", (), ()

    positives = [f"{name} = {value:.2f} (healthy)" for name, value in resolved.items() if positive_predicates[name](value)]
    all_positive = len(positives) == len(resolved)

    if len(resolved) >= _MIN_RESOLVED_RATIOS_FOR_HOLD_CANDIDATE and all_positive:
        return "HOLD_CANDIDATE", tuple(positives), ()
    return "WATCH", tuple(positives), ()


def _evidence_grade(
    completed: CompletedOfferingTerms, derived: DerivedFinancialFeatures | None, market: MarketContextSnapshot | None
) -> EvidenceGrade:
    if _missing_critical_evidence(completed):
        return "NONE"
    supporting = 0
    if derived is not None:
        supporting += sum(1 for name in DERIVED_FINANCIAL_FEATURE_NAMES if getattr(derived, name).status == "computed")
    if market is not None:
        supporting += len(market.features)
    if supporting == 0:
        return "WEAK"
    if supporting <= 3:
        return "MODERATE"
    return "STRONG"


def _window_closed(completed: CompletedOfferingTerms, as_of: datetime) -> bool:
    end_field = completed.get("subscription_end")
    if end_field.effective_status != "extracted":
        return False
    return end_field.effective_value < as_of.date()  # type: ignore[operator]


def evaluate_subscription_decision(inputs: SubscriptionDecisionInputs) -> SubscriptionDecisionV1:
    completed = inputs.completed_terms
    evidence_grade = _evidence_grade(completed, inputs.derived_financials, inputs.market_context)
    missing = _missing_critical_evidence(completed)

    if missing:
        return SubscriptionDecisionV1(
            action="CANNOT_ASSESS_SUBSCRIPTION",
            subscription_edge="UNKNOWN",
            intended_horizon=_HORIZON_BY_ACTION["CANNOT_ASSESS_SUBSCRIPTION"],
            evidence_grade=evidence_grade,
            ownership_view="NOT_ASSESSABLE",
            allocation_scenarios=(),
            strongest_positive_evidence=(),
            strongest_risks=(),
            missing_critical_evidence=missing,
            reasons=(f"missing critical subscription evidence: {', '.join(missing)}",),
            rule_version=RULE_VERSION,
        )

    red_flag_triggered, red_flag_reasons = _evaluate_red_flags(inputs.offering_terms, completed, inputs.disclosures)
    edge, edge_reasons = _subscription_edge(completed, red_flag_triggered)
    ownership_view, ownership_positives, ownership_risks = _ownership_view(inputs.derived_financials)
    window_closed = _window_closed(completed, inputs.as_of)

    effective_terms = effective_offering_terms(inputs.offering_terms, completed)
    scenarios = tuple(
        build_allocation_scenario(effective_terms, count) for count in DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS
    )

    watchworthy = ownership_view in ("HOLD_CANDIDATE", "WATCH")

    if red_flag_triggered:
        action: SubscriptionAction = "PASS_SUBSCRIPTION"
    elif edge == "UNFAVORABLE":
        action = "PASS_AND_REASSESS_AFTER_LISTING" if watchworthy else "PASS_SUBSCRIPTION"
    elif window_closed:
        action = "PASS_AND_REASSESS_AFTER_LISTING" if watchworthy else "PASS_SUBSCRIPTION"
    else:
        action = "SUBSCRIBE_WITH_HOLD_OPTION" if ownership_view == "HOLD_CANDIDATE" else "SUBSCRIBE_FOR_LISTING_TRADE"

    reasons = list(edge_reasons)
    if window_closed:
        reasons.append(f"subscription window already closed as of {inputs.as_of.date().isoformat()}")
    if not red_flag_triggered and not reasons:
        reasons.append("core subscription mechanics resolved with no red flags")

    return SubscriptionDecisionV1(
        action=action,
        subscription_edge=edge,
        intended_horizon=_HORIZON_BY_ACTION[action],
        evidence_grade=evidence_grade,
        ownership_view=ownership_view,
        allocation_scenarios=scenarios,
        strongest_positive_evidence=ownership_positives,
        strongest_risks=tuple(list(red_flag_reasons) + list(ownership_risks)),
        missing_critical_evidence=(),
        reasons=tuple(reasons),
        rule_version=RULE_VERSION,
    )


def subscription_decision_as_dict(decision: SubscriptionDecisionV1) -> dict:
    from ..kap.allocation_scenario import allocation_scenario_as_dict

    return {
        "action": decision.action,
        "subscription_edge": decision.subscription_edge,
        "intended_horizon": decision.intended_horizon,
        "evidence_grade": decision.evidence_grade,
        "ownership_view": decision.ownership_view,
        "allocation_scenarios": [allocation_scenario_as_dict(s) for s in decision.allocation_scenarios],
        "strongest_positive_evidence": list(decision.strongest_positive_evidence),
        "strongest_risks": list(decision.strongest_risks),
        "missing_critical_evidence": list(decision.missing_critical_evidence),
        "reasons": list(decision.reasons),
        "rule_version": decision.rule_version,
    }


__all__ = [
    "DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS",
    "RULE_VERSION",
    "EvidenceGrade",
    "OwnershipView",
    "SubscriptionAction",
    "SubscriptionDecisionInputs",
    "SubscriptionDecisionV1",
    "SubscriptionEdge",
    "SubscriptionHorizon",
    "evaluate_subscription_decision",
    "subscription_decision_as_dict",
]
