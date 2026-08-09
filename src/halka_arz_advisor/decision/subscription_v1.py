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
:mod:`halka_arz_advisor.kap.derived_financials`,
:mod:`halka_arz_advisor.kap.valuation`,
:mod:`halka_arz_advisor.ipo_outcomes.regime`,
:mod:`halka_arz_advisor.decision.subscription_economics` (a sibling
module in this same package, composing this module's own
``AllocationScenario``/``ipo_outcomes`` inputs — not a new external
dependency), and :mod:`halka_arz_advisor.evds.models`.

**r2 (2026-08-08): hardened against several real economic overclaims r1
made** (see :data:`RULE_VERSION`) — r1 treated "equal distribution + a
substantial retail tranche" as `FAVORABLE` subscription edge, which is
an allocation-*mechanics* description, not evidence about whether
subscribing is actually a good trade. This version separates the two
concepts entirely:

- :func:`_mechanics_state` describes allocation mechanics only
  (``SUPPORTIVE``/``NEUTRAL``/``CONSTRAINED``/``UNKNOWN``, from
  ``OfferingTerms`` alone) and never determines ``subscription_edge`` on
  its own. A `"proportional"` distribution rule is *not* automatically
  ``CONSTRAINED`` merely because exact lots are harder to estimate in
  advance — only a genuinely thin retail tranche is.
- :func:`_subscription_edge` now reads only
  :class:`~halka_arz_advisor.ipo_outcomes.regime.RecentIpoRegime` — a
  leakage-safe, point-in-time aggregate of how *other*, already-settled
  recent IPOs performed over their first trading week (never the target
  IPO's own outcome; see that module's own docstring for the safety
  argument). Without enough mature comparables, ``subscription_edge``
  stays ``"UNKNOWN"`` — never inferred from mechanics alone.
- Evidence is now two separate grades
  (``subscription_evidence_grade``/``ownership_evidence_grade``),
  each counting only the inputs that dimension's own decision logic
  actually reads — market-context/BIST data inflates neither, since
  neither reads it.
- ``ownership_view`` no longer follows from healthy financial ratios
  alone. :func:`_financial_quality` (``POSITIVE``/``MIXED``/
  ``NEGATIVE``/``UNKNOWN``) is a pure ratio read; ``HOLD_CANDIDATE``
  additionally requires a resolved valuation anchor. Healthy ratios with
  no valuation anchor cap ``ownership_view`` at ``WATCH``.
- ``intended_horizon`` no longer implies a same-day, price-limit-immune
  "listing day flip" — see :data:`_HORIZON_BY_ACTION`.
- A new ``WATCH_SUBSCRIPTION`` action represents "terms are resolved
  and there's no blocker, but no positive subscription edge is
  established" — the previous version defaulted this exact case to
  ``SUBSCRIBE_FOR_LISTING_TRADE``, manufacturing a recommendation from
  an absence of evidence.

**r3 (2026-08-08): added a canonical pre-offer valuation-sanity layer**
(:mod:`halka_arz_advisor.kap.valuation`) — implied post-money market cap
**at the actual offer price**, plus P/E, P/S, P/B (and, when this
project ever extracts a depreciation/amortization figure, EV/EBITDA).
:func:`_valuation_anchor_available` now reads that module's
``ValuationEvidence.sufficiency`` exclusively — it no longer reads
:mod:`halka_arz_advisor.kap.derived_financials`'s own ``recalculated_pe``
(a *different*, still-untouched valuation reading anchored on the price
determination report's own proposed market cap, not the final offer
price; the two modules intentionally never share or duplicate a
calculation). ``valuation_evidence`` is now a normal field on this
module's result, so a human can see exactly which multiples were
computed, which weren't and why, and whether the evidence is sufficient
for a price sanity check — this module never computes or exposes a
cheap/expensive verdict, and neither does ``kap.valuation``.

**r4 (2026-08-09): added TL-denominated subscription economics**
(:mod:`halka_arz_advisor.decision.subscription_economics`) — for each of
this module's own ``allocation_scenarios``, roughly how much capital
that demand scenario would actually require and what a handful of
plausible post-listing return scenarios would mean in TL. Grounded in
real, other-IPOs' actual 5-day returns (the same leakage-safe
``ipo_outcomes.regime`` selection ``subscription_edge`` already reads)
when there's a defensible sample, otherwise a small, clearly labeled
illustrative band — never a new expected-return forecast. This is pure
downstream arithmetic with no verdict of its own: it never feeds
``action``/``subscription_edge``/``mechanics_state``, exactly like
``allocation_scenarios`` before it. ``personal_capital`` is a new,
optional single-number input (never a portfolio) purely to annotate
whether a scenario's required capital is economically meaningful for
the caller.

**Gates, not points** (unchanged in spirit from r1). Every rule below is
a pass/fail gate evaluated in a fixed order; nothing here sums or
averages a "score" across rules, so a strong result on one axis can
never buy back a failure on another — see :func:`evaluate_subscription_decision`
for the exact order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..evds.models import MarketContextSnapshot
from ..ipo_outcomes.models import IpoMarketOutcome
from ..ipo_outcomes.regime import RecentIpoRegime, STRONG_EVIDENCE_MATURE_IPO_COUNT, build_recent_ipo_regime
from ..kap.allocation_scenario import AllocationScenario, build_allocation_scenario
from ..kap.derived_financials import DerivedFinancialFeatures
from ..kap.manual_confirmation import CompletedOfferingTerms, effective_offering_terms
from ..kap.models import KapDisclosure
from ..kap.offering_terms import OfferingTerms
from ..kap.text import fold_turkish
from ..kap.valuation import ValuationEvidence
from .subscription_economics import PersonalCapitalContext, SubscriptionEconomics, build_subscription_economics

RULE_VERSION = "subscription_v1_r4"

SubscriptionAction = Literal[
    "SUBSCRIBE_FOR_LISTING_TRADE",
    "SUBSCRIBE_WITH_HOLD_OPTION",
    "WATCH_SUBSCRIPTION",
    "PASS_SUBSCRIPTION",
    "PASS_AND_REASSESS_AFTER_LISTING",
    "CANNOT_ASSESS_SUBSCRIPTION",
]
SubscriptionEdge = Literal["FAVORABLE", "NEUTRAL", "UNFAVORABLE", "UNKNOWN"]
MechanicsState = Literal["SUPPORTIVE", "NEUTRAL", "CONSTRAINED", "UNKNOWN"]
FinancialQuality = Literal["POSITIVE", "MIXED", "NEGATIVE", "UNKNOWN"]
OwnershipView = Literal["HOLD_CANDIDATE", "WATCH", "AVOID_LONG_TERM", "NOT_ASSESSABLE"]
EvidenceGrade = Literal["STRONG", "MODERATE", "WEAK", "NONE"]
SubscriptionHorizon = Literal[
    "5D_LISTING_TRADE", "5D_LISTING_TRADE_OR_HOLD", "watch_pending_edge", "watch_post_listing", "not_applicable"
]

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
# guaranteed-access tranche under equal distribution. These describe
# *mechanics* only (see _mechanics_state) — they no longer feed
# subscription_edge at all (see module docstring).
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
_MIN_RESOLVED_RATIOS_FOR_POSITIVE = 2

# A handful of round, illustrative hypothetical retail-participant
# counts spanning a small/medium/large demand scenario — never a
# forecast of actual demand (see kap.allocation_scenario's own module
# docstring: the count is always a caller-supplied what-if), and never
# read by any function in this module that decides action/edge/
# ownership — purely descriptive output.
DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS: tuple[int, ...] = (50_000, 200_000, 500_000)

# Same generic Turkish withdrawal/cancellation/postponement keyword set
# halka_arz_advisor.decision.engine's own hard rule uses — reimplemented
# here (not imported) to keep this module fully independent of
# expert_v0's engine module, per this module's own docstring.
_WITHDRAWAL_KEYWORDS: tuple[str, ...] = ("iptal", "vazgecme", "vazgecti", "erteleme", "durduruldu", "askiya alin")

_HORIZON_BY_ACTION: dict[SubscriptionAction, SubscriptionHorizon] = {
    # Named after the one evidence window this project can actually
    # back with real data (RecentIpoRegime's own 5-trading-day read) —
    # never implies a same-day, BIST-price-limit-immune exit.
    "SUBSCRIBE_FOR_LISTING_TRADE": "5D_LISTING_TRADE",
    "SUBSCRIBE_WITH_HOLD_OPTION": "5D_LISTING_TRADE_OR_HOLD",
    "WATCH_SUBSCRIPTION": "watch_pending_edge",
    "PASS_SUBSCRIPTION": "not_applicable",
    "PASS_AND_REASSESS_AFTER_LISTING": "watch_post_listing",
    "CANNOT_ASSESS_SUBSCRIPTION": "not_applicable",
}


@dataclass(frozen=True, slots=True)
class SubscriptionDecisionInputs:
    offering_terms: OfferingTerms
    completed_terms: CompletedOfferingTerms
    derived_financials: DerivedFinancialFeatures | None
    # Canonical valuation evidence (see kap.valuation) — the sole
    # source _valuation_anchor_available reads; never rebuilt or
    # duplicated here.
    valuation_evidence: ValuationEvidence
    market_context: MarketContextSnapshot | None
    as_of: datetime
    ticker: str | None = None
    # OTHER IPOs' already-cached outcomes (never the target's own) —
    # see ipo_outcomes.regime.build_recent_ipo_regime, which this
    # module calls with `exclude_ticker=ticker` to make the exclusion
    # structural rather than a caller obligation.
    recent_ipo_outcomes: tuple[IpoMarketOutcome, ...] = ()
    disclosures: tuple[KapDisclosure, ...] = ()
    # Optional, investor-level (never per-position/portfolio) capital
    # figure — see subscription_economics.PersonalCapitalContext's own
    # docstring for why this stays a single number.
    personal_capital: PersonalCapitalContext | None = None


@dataclass(frozen=True, slots=True)
class SubscriptionDecisionV1:
    action: SubscriptionAction
    subscription_edge: SubscriptionEdge
    mechanics_state: MechanicsState
    intended_horizon: SubscriptionHorizon
    subscription_evidence_grade: EvidenceGrade
    ownership_evidence_grade: EvidenceGrade
    financial_quality: FinancialQuality
    ownership_view: OwnershipView
    recent_ipo_regime: RecentIpoRegime
    valuation_evidence: ValuationEvidence
    allocation_scenarios: tuple[AllocationScenario, ...]
    subscription_economics: SubscriptionEconomics
    manually_confirmed_fields: tuple[str, ...]
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


def _mechanics_state(completed: CompletedOfferingTerms) -> tuple[MechanicsState, tuple[str, ...]]:
    """Describes allocation mechanics only — from ``OfferingTerms``
    alone, never from ``RecentIpoRegime``/market data, and never itself
    read by :func:`_subscription_edge` (see module docstring)."""
    rule_field = completed.get("retail_distribution_rule")
    if rule_field.effective_status != "extracted":
        return "UNKNOWN", ("retail_distribution_rule is not resolved (automatically or manually)",)

    pct_field = completed.get("retail_allocation_percentage")
    pct = float(pct_field.effective_value) if pct_field.effective_status == "extracted" else None  # type: ignore[arg-type]

    if pct is not None and pct < _RETAIL_ALLOCATION_THIN_PCT:
        return "CONSTRAINED", (f"retail tranche is thin: only {pct:.1f}% of the offering is allocated to retail investors",)

    rule_label = "equal" if rule_field.effective_value == "equal" else "proportional"
    if rule_field.effective_value == "equal" and pct is not None and pct >= _RETAIL_ALLOCATION_SUBSTANTIAL_PCT:
        return "SUPPORTIVE", (f"equal distribution with a substantial {pct:.1f}% retail tranche",)

    size_desc = "an unknown-size" if pct is None else f"a {pct:.1f}%"
    return "NEUTRAL", (
        f"{rule_label} distribution with {size_desc} retail tranche — describes allocation mechanics only, "
        "not whether subscribing is a good trade",
    )


def _subscription_edge(regime: RecentIpoRegime) -> tuple[SubscriptionEdge, tuple[str, ...]]:
    """Reads only ``RecentIpoRegime`` — never ``OfferingTerms``
    mechanics, never market context (see module docstring)."""
    if regime.status == "UNKNOWN":
        return "UNKNOWN", (
            f"only {regime.mature_ipo_count} mature comparable recent IPO(s) in the last {regime.window_days} "
            "day(s) — too few to read a short-term subscription regime",
        )
    share = regime.positive_bist_relative_share_5d
    detail = f"{regime.mature_ipo_count} mature recent IPOs in the last {regime.window_days} day(s), {share:.0%} with a positive 5d BIST-relative return"  # type: ignore[str-format]
    if regime.status == "FAVORABLE":
        return "FAVORABLE", (detail,)
    if regime.status == "UNFAVORABLE":
        return "UNFAVORABLE", (detail,)
    return "NEUTRAL", (detail,)


def _financial_quality(derived: DerivedFinancialFeatures | None) -> tuple[FinancialQuality, tuple[str, ...], tuple[str, ...]]:
    """A pure ratio read — never market_context, never a valuation
    judgment (see :func:`_valuation_anchor_available` for that,
    separately)."""
    if derived is None:
        return "UNKNOWN", (), ()

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
        return "NEGATIVE", (), tuple(risks)

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
        return "UNKNOWN", (), ()

    positives = [f"{name} = {value:.2f} (healthy)" for name, value in resolved.items() if positive_predicates[name](value)]
    all_positive = len(positives) == len(resolved)
    if len(resolved) >= _MIN_RESOLVED_RATIOS_FOR_POSITIVE and all_positive:
        return "POSITIVE", tuple(positives), ()
    return "MIXED", tuple(positives), ()


def _valuation_anchor_available(valuation: ValuationEvidence) -> bool:
    """Reads only ``kap.valuation.ValuationEvidence.sufficiency`` — the
    canonical valuation evidence — never ``kap.derived_financials``'s
    own ``recalculated_pe`` (a different anchor; see module docstring)
    and never recomputes anything itself."""
    return valuation.sufficiency == "SUFFICIENT"


def _ownership_view(financial_quality: FinancialQuality, valuation_anchor_available: bool) -> OwnershipView:
    if financial_quality == "NEGATIVE":
        return "AVOID_LONG_TERM"
    if financial_quality == "UNKNOWN":
        return "NOT_ASSESSABLE"
    if financial_quality == "MIXED":
        return "WATCH"
    # POSITIVE: still requires a valuation anchor for HOLD_CANDIDATE —
    # healthy ratios alone are not enough (see module docstring).
    return "HOLD_CANDIDATE" if valuation_anchor_available else "WATCH"


def _subscription_evidence_grade(missing_critical: tuple[str, ...], regime: RecentIpoRegime) -> EvidenceGrade:
    """Counts only what :func:`_missing_critical_evidence`/
    :func:`_subscription_edge` actually read — never market_context,
    never financial ratios (those belong to
    :func:`_ownership_evidence_grade` instead, per "a feature not used
    by a dimension's decision logic must not inflate that dimension's
    evidence grade")."""
    if missing_critical:
        return "NONE"
    if regime.status == "UNKNOWN":
        return "WEAK"
    if regime.mature_ipo_count < STRONG_EVIDENCE_MATURE_IPO_COUNT:
        return "MODERATE"
    return "STRONG"


def _ownership_evidence_grade(
    financial_quality: FinancialQuality, valuation_anchor_available: bool, derived: DerivedFinancialFeatures | None
) -> EvidenceGrade:
    """Counts only what :func:`_financial_quality`/
    :func:`_valuation_anchor_available` actually read — never
    subscription-mechanics/regime fields."""
    if derived is None:
        return "NONE"
    if financial_quality == "UNKNOWN":
        return "WEAK"
    return "STRONG" if valuation_anchor_available else "MODERATE"


def _window_closed(completed: CompletedOfferingTerms, as_of: datetime) -> bool:
    end_field = completed.get("subscription_end")
    if end_field.effective_status != "extracted":
        return False
    return end_field.effective_value < as_of.date()  # type: ignore[operator]


def evaluate_subscription_decision(inputs: SubscriptionDecisionInputs) -> SubscriptionDecisionV1:
    completed = inputs.completed_terms
    missing = _missing_critical_evidence(completed)
    manually_confirmed = tuple(sorted(name for name, field in completed.as_dict().items() if field.source == "user_confirmed"))

    # Every one of these is computed unconditionally, even if the
    # subscription critical-evidence gate below will force
    # CANNOT_ASSESS_SUBSCRIPTION — ownership/regime context are
    # independent dimensions a human reviewing the card can still use
    # (e.g. to decide whether completing the missing fields is even
    # worth doing), matching "ownership view may remain assessable (or
    # not) independent of the subscription action" from this module's
    # own design.
    regime = build_recent_ipo_regime(inputs.recent_ipo_outcomes, as_of=inputs.as_of, exclude_ticker=inputs.ticker)
    subscription_edge, edge_reasons = _subscription_edge(regime)
    mechanics_state, mechanics_reasons = _mechanics_state(completed)
    financial_quality, fin_positives, fin_risks = _financial_quality(inputs.derived_financials)
    valuation_anchor = _valuation_anchor_available(inputs.valuation_evidence)
    ownership_view = _ownership_view(financial_quality, valuation_anchor)

    subscription_grade = _subscription_evidence_grade(missing, regime)
    ownership_grade = _ownership_evidence_grade(financial_quality, valuation_anchor, inputs.derived_financials)

    red_flag_triggered, red_flag_reasons = _evaluate_red_flags(inputs.offering_terms, completed, inputs.disclosures)
    window_closed = _window_closed(completed, inputs.as_of)

    effective_terms = effective_offering_terms(inputs.offering_terms, completed)
    scenarios = tuple(
        build_allocation_scenario(effective_terms, count) for count in DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS
    )
    economics = build_subscription_economics(
        scenarios,
        recent_ipo_outcomes=inputs.recent_ipo_outcomes,
        as_of=inputs.as_of,
        exclude_ticker=inputs.ticker,
        personal_capital=inputs.personal_capital,
    )

    watchworthy = ownership_view in ("HOLD_CANDIDATE", "WATCH")

    # Gates, not points — fixed order, first match wins; see module
    # docstring for why each branch exists and what it protects
    # against.
    action: SubscriptionAction
    if missing:
        action = "CANNOT_ASSESS_SUBSCRIPTION"
    elif red_flag_triggered:
        action = "PASS_SUBSCRIPTION"
    elif window_closed:
        action = "PASS_AND_REASSESS_AFTER_LISTING" if watchworthy else "PASS_SUBSCRIPTION"
    elif subscription_edge == "UNFAVORABLE":
        action = "PASS_AND_REASSESS_AFTER_LISTING" if watchworthy else "PASS_SUBSCRIPTION"
    elif mechanics_state == "CONSTRAINED":
        # A thin retail tranche is a real capacity constraint, but not
        # by itself "clearly unfavorable evidence" — WATCH, not PASS.
        action = "WATCH_SUBSCRIPTION"
    elif subscription_edge == "FAVORABLE":
        action = "SUBSCRIBE_WITH_HOLD_OPTION" if ownership_view == "HOLD_CANDIDATE" else "SUBSCRIBE_FOR_LISTING_TRADE"
    else:
        # Resolved terms, no blocker, mechanics not prohibitive — but
        # subscription_edge is NEUTRAL or UNKNOWN: resolved mechanics
        # alone must never manufacture a SUBSCRIBE signal.
        action = "WATCH_SUBSCRIPTION"

    reasons: list[str] = []
    if missing:
        reasons.append(f"missing critical subscription evidence: {', '.join(missing)}")
    reasons.extend(red_flag_reasons)
    if window_closed:
        reasons.append(f"subscription window already closed as of {inputs.as_of.date().isoformat()}")
    reasons.extend(edge_reasons)
    reasons.extend(mechanics_reasons)
    if financial_quality == "POSITIVE" and not valuation_anchor:
        reasons.append(
            f"financial ratios are healthy, but valuation evidence is insufficient for a price sanity check "
            f"({inputs.valuation_evidence.sufficiency_reason}) — ownership capped at WATCH, not HOLD_CANDIDATE"
        )
    if not reasons:
        reasons.append("core subscription mechanics resolved with no blockers, but no subscription edge is established")

    strongest_positive = (
        tuple(fin_positives)
        + (mechanics_reasons if mechanics_state == "SUPPORTIVE" else ())
        + (edge_reasons if subscription_edge == "FAVORABLE" else ())
    )
    strongest_risks = (
        tuple(red_flag_reasons)
        + tuple(fin_risks)
        + (edge_reasons if subscription_edge == "UNFAVORABLE" else ())
        + (mechanics_reasons if mechanics_state == "CONSTRAINED" else ())
    )

    return SubscriptionDecisionV1(
        action=action,
        subscription_edge=subscription_edge,
        mechanics_state=mechanics_state,
        intended_horizon=_HORIZON_BY_ACTION[action],
        subscription_evidence_grade=subscription_grade,
        ownership_evidence_grade=ownership_grade,
        financial_quality=financial_quality,
        ownership_view=ownership_view,
        recent_ipo_regime=regime,
        valuation_evidence=inputs.valuation_evidence,
        allocation_scenarios=scenarios,
        subscription_economics=economics,
        manually_confirmed_fields=manually_confirmed,
        strongest_positive_evidence=strongest_positive,
        strongest_risks=strongest_risks,
        missing_critical_evidence=missing,
        reasons=tuple(reasons),
        rule_version=RULE_VERSION,
    )


def subscription_decision_as_dict(decision: SubscriptionDecisionV1) -> dict:
    from ..kap.allocation_scenario import allocation_scenario_as_dict
    from ..kap.valuation import valuation_evidence_as_dict
    from .subscription_economics import subscription_economics_as_dict

    return {
        "action": decision.action,
        "subscription_edge": decision.subscription_edge,
        "mechanics_state": decision.mechanics_state,
        "intended_horizon": decision.intended_horizon,
        "subscription_evidence_grade": decision.subscription_evidence_grade,
        "ownership_evidence_grade": decision.ownership_evidence_grade,
        "financial_quality": decision.financial_quality,
        "ownership_view": decision.ownership_view,
        "recent_ipo_regime": {
            "status": decision.recent_ipo_regime.status,
            "mature_ipo_count": decision.recent_ipo_regime.mature_ipo_count,
            "median_bist_relative_return_5d": decision.recent_ipo_regime.median_bist_relative_return_5d,
            "positive_bist_relative_share_5d": decision.recent_ipo_regime.positive_bist_relative_share_5d,
            "window_days": decision.recent_ipo_regime.window_days,
            "included_tickers": list(decision.recent_ipo_regime.included_tickers),
        },
        "valuation_evidence": valuation_evidence_as_dict(decision.valuation_evidence),
        "allocation_scenarios": [allocation_scenario_as_dict(s) for s in decision.allocation_scenarios],
        "subscription_economics": subscription_economics_as_dict(decision.subscription_economics),
        "manually_confirmed_fields": list(decision.manually_confirmed_fields),
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
    "FinancialQuality",
    "MechanicsState",
    "OwnershipView",
    # Re-exported for convenience: callers building SubscriptionDecisionInputs
    # need this to populate its optional personal_capital field, and it's
    # otherwise defined in the sibling subscription_economics module.
    "PersonalCapitalContext",
    "SubscriptionAction",
    "SubscriptionDecisionInputs",
    "SubscriptionDecisionV1",
    "SubscriptionEdge",
    "SubscriptionHorizon",
    "evaluate_subscription_decision",
    "subscription_decision_as_dict",
]
