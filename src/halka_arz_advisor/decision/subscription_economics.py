"""Connects a hypothetical retail :class:`~halka_arz_advisor.kap.allocation_scenario.AllocationScenario`'s
TL capital exposure to (a) real historical context and (b) a fixed,
clearly-labeled stress illustration — and, when the caller supplies a
subscription capital limit, to what could actually be executed under
it.

:mod:`halka_arz_advisor.decision.subscription_v1` decides *whether* to
subscribe; this module answers a different, narrower question it
deliberately leaves open — "if I did, roughly how much money is
actually at stake, and what could I plausibly gain or lose in TL?" —
by composing evidence that already exists elsewhere, without touching
either:

- :mod:`halka_arz_advisor.kap.allocation_scenario` for how many shares
  (and how much TL) a hypothetical demand scenario implies.
- :mod:`halka_arz_advisor.ipo_outcomes.regime` for how *other*,
  already-settled recent IPOs actually performed — via
  :func:`~halka_arz_advisor.ipo_outcomes.regime.select_mature_outcomes`,
  the exact same leakage-safe, point-in-time selection
  ``decision.subscription_v1``'s own ``RecentIpoRegime`` read uses, so
  this module can never see more (or different) evidence than that one
  already treats as safe.

No new extraction, no new participant-count forecasting (a demand
scenario's hypothetical participant count is always the caller's
what-if input, exactly as in ``kap.allocation_scenario`` itself — never
predicted here), and no new expected-return model.

**Historical observation and stress illustration are two structurally
separate things, never blended into one "return scenario" concept.**
An earlier version of this module read a small recent-IPO cohort's own
worst/median/best actual return as if it were this IPO's own plausible
loss range — if every recent comparable happened to be positive, that
made the "worst case" read as a positive number, which is not a
downside scenario at all, just an artifact of a small, currently-
favorable sample. This version keeps both pieces, clearly separated:

- :func:`build_historical_observation` — the real worst/median/best
  ``return_5d`` among other, already-settled recent comparable IPOs
  (see :class:`HistoricalObservation`), reported purely as *what already
  happened*, with the comparable count always shown alongside it so a
  human can judge the sample size themselves. Never converted to a
  TL profit/loss, and never labeled a "scenario" — nothing here implies
  it bounds this IPO's own future return in either direction.
- :func:`build_stress_scenarios` — a small, fixed, stated
  capital-at-risk illustration (:data:`STRESS_RETURN_SCENARIOS`), the
  same regardless of what the recent cohort happened to do. This is
  the only thing priced into a TL profit/loss
  (:class:`StressOutcome`) — deliberately not a statistical
  value-at-risk estimate or a fitted distribution, just "here is what a
  stated illustrative move would mean in TL."

**Allocation uncertainty** (how many shares a demand scenario would
actually get you) stays exactly as ``AllocationScenario`` already
models it — a baseline plus a floor/remainder range, passed through
unchanged, never collapsed into a single confident number.

**Executable allocation.** When the caller supplies a
:class:`SubscriptionCapitalLimit`, each demand scenario also gets an
:class:`ExecutableAllocation` — the whole-share count actually fundable
with that capital (never more than the scenario's own theoretical
allocation, never a fractional share), and whether capital was the
binding constraint. Every TL profit/loss (:class:`StressOutcome`) is
then priced on the *executable* capital when a limit was supplied —
never on theoretical capital the caller does not actually have.

Equal-distribution allocation mechanics are not, and must never be
read as, an investment edge: this module attaches no verdict, edge, or
recommendation of its own — it is pure downstream arithmetic over
figures ``decision.subscription_v1`` (edge/mechanics) already keeps
separate, and never feeds back into that module's own action/edge
logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from ..ipo_outcomes.models import IpoMarketOutcome
from ..ipo_outcomes.regime import DEFAULT_LOOKBACK_DAYS, select_mature_outcomes
from ..kap.allocation_scenario import AllocationScenario

SUBSCRIPTION_ECONOMICS_VERSION = "subscription_economics_r2"

# A small, fixed, stated capital-at-risk illustration — never fit
# against this project's own ipo_outcomes data, and never influenced by
# what the recent comparable cohort happened to do (see module
# docstring for why the two must stay separate). Not a statistical
# value-at-risk estimate or a forecast of any kind; every label says so
# explicitly ("gösterge" = illustrative/indicative).
STRESS_RETURN_SCENARIOS: tuple[tuple[str, float], ...] = (
    ("Stres senaryosu (gösterge — sermaye riskini göstermek içindir)", -0.20),
    ("İyimser gösterge (gösterge — bir tahmin değildir)", 0.20),
)

_STRESS_SCENARIO_BASIS = (
    "fixed, stated illustrative move, not derived from any historical sample and not a statistical "
    "value-at-risk estimate or forecast — used only to show what a capital-at-risk move would mean in TL."
)

# Labels for kap.allocation_scenario.DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS'
# ascending-participant-count ordering (fewer hypothetical participants
# -> more shares per participant -> "low demand"). Positional, not
# keyed by the exact counts, so a future change to those counts can't
# silently mislabel a scenario.
DEMAND_LABELS_ASCENDING: tuple[str, ...] = ("Düşük talep", "Tipik talep", "Yüksek talep")


@dataclass(frozen=True, slots=True)
class HistoricalObservation:
    """Real, already-realized 5-day returns of other, already-settled
    recent comparable IPOs. Pure historical fact-reporting — never a
    prediction, never a claimed downside/upside bound for this IPO's
    own future return, and never priced into a TL figure (that is what
    :class:`StressOutcome` is for, deliberately kept separate)."""

    comparable_count: int
    observed_worst_pct: float | None
    observed_median_pct: float | None
    observed_best_pct: float | None
    basis: str


@dataclass(frozen=True, slots=True)
class StressScenario:
    """One fixed, stated illustrative post-listing return, as a
    fraction (``-0.20`` == -20%) — never a probability-weighted
    expectation and never derived from ``HistoricalObservation``."""

    label: str
    return_pct: float
    basis: str


@dataclass(frozen=True, slots=True)
class StressOutcome:
    scenario: StressScenario
    profit_loss_tl: float


@dataclass(frozen=True, slots=True)
class ExecutableAllocation:
    """The whole-share allocation actually fundable with a supplied
    :class:`SubscriptionCapitalLimit` — never more than the demand
    scenario's own theoretical ``base_integer_allocation``, and never a
    fractional share (``shares`` is computed by floor division on
    price, exactly like ``AllocationScenario`` itself floors on
    participant count)."""

    shares: int
    capital_tl: float
    capital_constrained: bool


@dataclass(frozen=True, slots=True)
class AllocationEconomics:
    """One demand scenario's theoretical shares/capital (from
    ``AllocationScenario``, unchanged), what could actually be executed
    under a supplied capital limit (if any), and what the fixed stress
    scenarios would mean in TL on the *executable* capital when a limit
    was supplied, or on the theoretical capital otherwise. Empty
    ``stress_outcomes`` when neither capital figure is resolved — never
    a guessed capital figure."""

    demand_label: str
    allocation_scenario: AllocationScenario
    theoretical_capital_tl: float | None
    theoretical_capital_tl_range: tuple[float, float] | None
    executable: ExecutableAllocation | None
    stress_outcomes: tuple[StressOutcome, ...]


@dataclass(frozen=True, slots=True)
class SubscriptionCapitalLimit:
    """How much TL the caller is willing/able to commit to *this one*
    subscription — deliberately not total personal wealth, and
    deliberately not a portfolio (position sizing, multi-IPO
    aggregation, risk budgets are all out of scope here)."""

    max_subscription_capital_tl: float


@dataclass(frozen=True, slots=True)
class SubscriptionEconomics:
    historical_observation: HistoricalObservation
    stress_scenarios: tuple[StressScenario, ...]
    allocations: tuple[AllocationEconomics, ...]
    version: str = SUBSCRIPTION_ECONOMICS_VERSION


def build_historical_observation(
    recent_ipo_outcomes: Sequence[IpoMarketOutcome],
    *,
    as_of: datetime,
    exclude_ticker: str | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> HistoricalObservation:
    """Pure — no I/O. Reports the real worst/median/best *raw* (not
    BIST-relative) 5-day return among other, already-settled recent
    comparable IPOs (the same leakage-safe selection
    :func:`~halka_arz_advisor.ipo_outcomes.regime.build_recent_ipo_regime`
    uses) — the figure that prices what an investor would have actually
    realized in TL, unlike the BIST-relative figure ``RecentIpoRegime``
    itself reads for its own, different purpose. Always reported
    alongside ``comparable_count`` so a human can judge the sample size
    themselves; never gated behind a minimum-evidence threshold, since
    this is raw fact-reporting, not a scenario this module (or any
    caller) treats as decision-grade evidence on its own."""
    mature = select_mature_outcomes(recent_ipo_outcomes, as_of=as_of, exclude_ticker=exclude_ticker, lookback_days=lookback_days)
    returns = sorted(o.return_5d for o in mature if o.return_5d is not None)

    if not returns:
        return HistoricalObservation(
            comparable_count=0,
            observed_worst_pct=None,
            observed_median_pct=None,
            observed_best_pct=None,
            basis=f"no other, already-settled recent comparable IPOs with a resolved 5-day return in the last {lookback_days} day(s) yet.",
        )

    basis = (
        f"actual 5-trading-day returns of {len(returns)} other, already-settled recent comparable IPO(s) in the "
        f"last {lookback_days} day(s) — real historical outcomes, not a prediction of this IPO's own return and "
        "not a downside/upside bound: a small, currently all-positive (or all-negative) cohort does not mean the "
        "same will hold here."
    )
    return HistoricalObservation(
        comparable_count=len(returns),
        observed_worst_pct=returns[0] / 100.0,
        observed_median_pct=median(returns) / 100.0,
        observed_best_pct=returns[-1] / 100.0,
        basis=basis,
    )


def build_stress_scenarios() -> tuple[StressScenario, ...]:
    """Pure, deterministic, and independent of any historical data —
    see :data:`STRESS_RETURN_SCENARIOS` and the module docstring for
    why this must never be derived from ``HistoricalObservation``."""
    return tuple(StressScenario(label=label, return_pct=pct, basis=_STRESS_SCENARIO_BASIS) for label, pct in STRESS_RETURN_SCENARIOS)


def _executable_allocation(
    allocation_scenario: AllocationScenario,
    *,
    offer_price: float | None,
    max_subscription_capital_tl: float | None,
) -> ExecutableAllocation | None:
    """``None`` when there's no capital limit to apply, or when either
    the theoretical share count or the offer price isn't resolved (see
    ``AllocationScenario.status``) — never a guessed share count."""
    if max_subscription_capital_tl is None:
        return None
    if allocation_scenario.status != "computed":
        return None
    theoretical_shares = allocation_scenario.base_integer_allocation
    if theoretical_shares is None or offer_price is None or offer_price <= 0:
        return None

    affordable_shares = max(int(max_subscription_capital_tl // offer_price), 0)
    shares = min(theoretical_shares, affordable_shares)
    return ExecutableAllocation(
        shares=shares,
        capital_tl=shares * offer_price,
        capital_constrained=shares < theoretical_shares,
    )


def build_allocation_economics(
    allocation_scenario: AllocationScenario,
    demand_label: str,
    stress_scenarios: Sequence[StressScenario],
    *,
    offer_price: float | None = None,
    max_subscription_capital_tl: float | None = None,
) -> AllocationEconomics:
    """Pure. Stress P&L is priced on the *executable* capital when a
    capital limit was supplied and resolvable, otherwise on the
    theoretical baseline capital — never on money the caller does not
    have (see module docstring)."""
    theoretical_capital = allocation_scenario.tl_allocation_baseline
    executable = _executable_allocation(
        allocation_scenario, offer_price=offer_price, max_subscription_capital_tl=max_subscription_capital_tl
    )

    pl_basis = executable.capital_tl if executable is not None else theoretical_capital
    stress_outcomes: tuple[StressOutcome, ...] = ()
    if pl_basis is not None:
        stress_outcomes = tuple(StressOutcome(scenario=s, profit_loss_tl=pl_basis * s.return_pct) for s in stress_scenarios)

    return AllocationEconomics(
        demand_label=demand_label,
        allocation_scenario=allocation_scenario,
        theoretical_capital_tl=theoretical_capital,
        theoretical_capital_tl_range=allocation_scenario.tl_allocation_range,
        executable=executable,
        stress_outcomes=stress_outcomes,
    )


def build_subscription_economics(
    allocation_scenarios: Sequence[AllocationScenario],
    *,
    recent_ipo_outcomes: Sequence[IpoMarketOutcome],
    as_of: datetime,
    exclude_ticker: str | None,
    offer_price: float | None = None,
    subscription_capital_limit: SubscriptionCapitalLimit | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> SubscriptionEconomics:
    """Pure — no I/O. ``allocation_scenarios`` is expected to be exactly
    what ``decision.subscription_v1`` already built (its own
    ``DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS`` scenarios); this
    function never builds its own."""
    historical = build_historical_observation(
        recent_ipo_outcomes, as_of=as_of, exclude_ticker=exclude_ticker, lookback_days=lookback_days
    )
    stress_scenarios = build_stress_scenarios()

    if len(allocation_scenarios) == len(DEMAND_LABELS_ASCENDING):
        labels: Sequence[str] = DEMAND_LABELS_ASCENDING
    else:
        labels = tuple(
            f"Senaryo {i + 1} ({scenario.hypothetical_retail_participant_count:,} katılımcı varsayımı)".replace(",", ".")
            for i, scenario in enumerate(allocation_scenarios)
        )

    max_capital = subscription_capital_limit.max_subscription_capital_tl if subscription_capital_limit is not None else None
    allocations = tuple(
        build_allocation_economics(
            scenario, label, stress_scenarios, offer_price=offer_price, max_subscription_capital_tl=max_capital
        )
        for scenario, label in zip(allocation_scenarios, labels)
    )

    return SubscriptionEconomics(
        historical_observation=historical,
        stress_scenarios=stress_scenarios,
        allocations=allocations,
    )


def subscription_economics_as_dict(economics: SubscriptionEconomics) -> dict:
    from ..kap.allocation_scenario import allocation_scenario_as_dict

    return {
        "historical_observation": {
            "comparable_count": economics.historical_observation.comparable_count,
            "observed_worst_pct": economics.historical_observation.observed_worst_pct,
            "observed_median_pct": economics.historical_observation.observed_median_pct,
            "observed_best_pct": economics.historical_observation.observed_best_pct,
            "basis": economics.historical_observation.basis,
        },
        "stress_scenarios": [
            {"label": s.label, "return_pct": s.return_pct, "basis": s.basis} for s in economics.stress_scenarios
        ],
        "allocations": [
            {
                "demand_label": allocation.demand_label,
                "allocation_scenario": allocation_scenario_as_dict(allocation.allocation_scenario),
                "theoretical_capital_tl": allocation.theoretical_capital_tl,
                "theoretical_capital_tl_range": list(allocation.theoretical_capital_tl_range)
                if allocation.theoretical_capital_tl_range
                else None,
                "executable": {
                    "shares": allocation.executable.shares,
                    "capital_tl": allocation.executable.capital_tl,
                    "capital_constrained": allocation.executable.capital_constrained,
                }
                if allocation.executable is not None
                else None,
                "stress_outcomes": [
                    {
                        "label": outcome.scenario.label,
                        "return_pct": outcome.scenario.return_pct,
                        "profit_loss_tl": outcome.profit_loss_tl,
                    }
                    for outcome in allocation.stress_outcomes
                ],
            }
            for allocation in economics.allocations
        ],
        "version": economics.version,
    }


__all__ = [
    "DEMAND_LABELS_ASCENDING",
    "STRESS_RETURN_SCENARIOS",
    "SUBSCRIPTION_ECONOMICS_VERSION",
    "AllocationEconomics",
    "ExecutableAllocation",
    "HistoricalObservation",
    "StressOutcome",
    "StressScenario",
    "SubscriptionCapitalLimit",
    "SubscriptionEconomics",
    "build_allocation_economics",
    "build_historical_observation",
    "build_stress_scenarios",
    "build_subscription_economics",
    "subscription_economics_as_dict",
]
