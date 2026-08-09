from datetime import date, datetime

from halka_arz_advisor.decision.subscription_economics import (
    DEMAND_LABELS_ASCENDING,
    SubscriptionCapitalLimit,
    build_historical_observation,
    build_stress_scenarios,
    build_subscription_economics,
)
from halka_arz_advisor.ipo_outcomes.models import IpoMarketOutcome
from halka_arz_advisor.ipo_outcomes.regime import STRONG_EVIDENCE_MATURE_IPO_COUNT
from halka_arz_advisor.kap.allocation_scenario import AllocationScenario

AS_OF = datetime(2026, 8, 10)
OFFER_PRICE = 45.0


def _outcome(ticker: str, return_5d: float | None, trading_start: date = date(2026, 7, 1)) -> IpoMarketOutcome:
    return IpoMarketOutcome(
        ticker=ticker, company_name=None, offer_price=10.0,
        resolved_trading_start_date=trading_start, spk_trading_start_date=trading_start,
        kap_trading_start_announcement_dates=(), trading_start_conflict=False,
        price_observation_count=10, last_price_observation_date=trading_start,
        first_day_return=5.0, return_5d=return_5d, return_20d=None, return_3m=None,
        max_drawdown_5d=None, max_drawdown_20d=None, max_drawdown_3m=None,
        bist_relative_first_day=5.0, bist_relative_5d=return_5d, bist_relative_20d=None, bist_relative_3m=None,
        generated_at=datetime(2026, 7, 1),
    )


def _computed_scenario(participant_count: int, base_shares: int, remainder: int, offer_price: float = OFFER_PRICE) -> AllocationScenario:
    range_shares = (base_shares, base_shares) if remainder == 0 else (base_shares, base_shares + 1)
    baseline = base_shares * offer_price
    tl_range = (baseline, baseline) if remainder == 0 else (baseline, (base_shares + 1) * offer_price)
    return AllocationScenario(
        hypothetical_retail_participant_count=participant_count,
        status="computed",
        average_shares_per_participant=float(base_shares),
        base_integer_allocation=base_shares,
        remainder_shares=remainder,
        allocation_range_shares=range_shares,
        tl_allocation_baseline=baseline,
        tl_allocation_range=tl_range,
        assumptions=("some assumption",),
        caveats=("some caveat",),
    )


def _unavailable_scenario(participant_count: int) -> AllocationScenario:
    return AllocationScenario(
        hypothetical_retail_participant_count=participant_count, status="unavailable",
        average_shares_per_participant=None, base_integer_allocation=None, remainder_shares=None,
        allocation_range_shares=None, tl_allocation_baseline=None, tl_allocation_range=None,
        assumptions=(), caveats=("blocked",),
    )


# --------------------------------------------------------------------------
# build_historical_observation — pure fact-reporting, never a scenario
# --------------------------------------------------------------------------


def test_no_comparables_reports_zero_count_not_a_guess():
    observation = build_historical_observation((), as_of=AS_OF, exclude_ticker=None)

    assert observation.comparable_count == 0
    assert observation.observed_worst_pct is None
    assert observation.observed_median_pct is None
    assert observation.observed_best_pct is None


def test_all_positive_cohort_reports_a_positive_worst_without_implying_downside_protection():
    # Every recent comparable happened to be positive — the observed
    # "worst" is still a positive number, and must be reported as such
    # (this is exactly what must never be read as "the downside case").
    outcomes = [_outcome(f"T{i}", v) for i, v in enumerate([5.0, 20.0, 40.0, 60.0, 61.0, 61.0])]

    observation = build_historical_observation(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert observation.comparable_count == 6
    assert observation.observed_worst_pct == 0.05
    assert observation.observed_best_pct == 0.61


def test_target_ipos_own_outcome_never_counted():
    outcomes = [_outcome("TARGET", 9999.0)] + [_outcome(f"T{i}", v) for i, v in enumerate([5.0, 20.0, 40.0])]

    observation = build_historical_observation(outcomes, as_of=AS_OF, exclude_ticker="TARGET")

    assert observation.comparable_count == 3
    assert observation.observed_best_pct == 0.40  # not skewed by TARGET's own 9999.0


def test_observation_is_reported_even_below_the_strong_evidence_bar():
    # Unlike a decision-grade "scenario", raw historical observation is
    # shown for whatever sample exists — the small N is reported
    # alongside it (comparable_count), not hidden behind a threshold.
    outcomes = [_outcome("A", 10.0)]

    observation = build_historical_observation(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert observation.comparable_count == 1
    assert observation.comparable_count < STRONG_EVIDENCE_MATURE_IPO_COUNT
    assert observation.observed_worst_pct == observation.observed_median_pct == observation.observed_best_pct == 0.10


# --------------------------------------------------------------------------
# build_stress_scenarios — fixed, never derived from historical data
# --------------------------------------------------------------------------


def test_stress_scenarios_include_a_negative_scenario_even_when_all_recent_ipos_were_positive():
    stress_scenarios = build_stress_scenarios()

    assert any(s.return_pct < 0 for s in stress_scenarios)


def test_stress_scenarios_are_identical_regardless_of_historical_data():
    # Never a function of recent_ipo_outcomes — same call signature has
    # no such parameter, which is itself the guarantee, but assert the
    # values are the same fixed constants across two independent calls.
    first = build_stress_scenarios()
    second = build_stress_scenarios()

    assert [s.return_pct for s in first] == [s.return_pct for s in second]


# --------------------------------------------------------------------------
# build_subscription_economics — allocation, historical vs. stress separation
# --------------------------------------------------------------------------


def test_historical_and_stress_are_reported_separately_even_with_an_all_positive_cohort():
    scenarios = (_computed_scenario(200_000, base_shares=14, remainder=0),)
    outcomes = [_outcome(f"T{i}", v) for i, v in enumerate([5.0, 20.0, 40.0, 60.0, 61.0, 61.0])]

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=outcomes, as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
    )

    # historical observation reflects the real (all-positive) cohort...
    assert economics.historical_observation.observed_worst_pct == 0.05
    # ...but the stress scenario is still a fixed, independent downside,
    # never derived from — or overridden by — that positive cohort.
    assert any(s.return_pct < 0 for s in economics.stress_scenarios)
    stress_pcts = {s.scenario.return_pct for s in economics.allocations[0].stress_outcomes}
    assert any(p < 0 for p in stress_pcts)


def test_three_scenarios_get_ascending_demand_labels_and_per_scenario_theoretical_capital():
    scenarios = (
        _computed_scenario(50_000, base_shares=35, remainder=0),
        _computed_scenario(200_000, base_shares=14, remainder=0),
        _computed_scenario(500_000, base_shares=6, remainder=0),
    )

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
    )

    assert [a.demand_label for a in economics.allocations] == list(DEMAND_LABELS_ASCENDING)
    low, typical, high = economics.allocations
    assert low.theoretical_capital_tl == 35 * OFFER_PRICE
    assert typical.theoretical_capital_tl == 14 * OFFER_PRICE
    assert high.theoretical_capital_tl == 6 * OFFER_PRICE
    # without a capital limit, stress P&L is priced on the theoretical capital
    for allocation in economics.allocations:
        assert allocation.executable is None
        for outcome in allocation.stress_outcomes:
            assert outcome.profit_loss_tl == allocation.theoretical_capital_tl * outcome.scenario.return_pct


def test_unavailable_allocation_scenario_yields_no_stress_outcomes_not_a_guess():
    scenarios = (_unavailable_scenario(50_000),)

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
    )

    assert economics.allocations[0].theoretical_capital_tl is None
    assert economics.allocations[0].executable is None
    assert economics.allocations[0].stress_outcomes == ()


# --------------------------------------------------------------------------
# Executable allocation under a subscription capital limit
# --------------------------------------------------------------------------


def test_capital_limit_below_theoretical_cost_caps_executable_shares_by_whole_share_floor_division():
    # theoretical: 35 shares * 45 TL = 1,575 TL. With only 500 TL:
    # floor(500 / 45) = 11 shares (11*45=495 TL), never 11.11 shares.
    scenarios = (_computed_scenario(50_000, base_shares=35, remainder=0),)

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
        subscription_capital_limit=SubscriptionCapitalLimit(500.0),
    )

    executable = economics.allocations[0].executable
    assert executable is not None
    assert executable.shares == 11
    assert executable.capital_tl == 11 * OFFER_PRICE
    assert executable.capital_constrained is True
    # P&L must be priced on the executable 495 TL, not the theoretical 1,575 TL
    for outcome in economics.allocations[0].stress_outcomes:
        assert outcome.profit_loss_tl == executable.capital_tl * outcome.scenario.return_pct


def test_capital_limit_above_theoretical_cost_is_not_constrained():
    scenarios = (_computed_scenario(500_000, base_shares=6, remainder=0),)  # 6 * 45 = 270 TL

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
        subscription_capital_limit=SubscriptionCapitalLimit(5000.0),
    )

    executable = economics.allocations[0].executable
    assert executable is not None
    assert executable.shares == 6
    assert executable.capital_tl == 270.0
    assert executable.capital_constrained is False


def test_theoretical_zero_shares_stays_zero_regardless_of_available_capital():
    scenarios = (_computed_scenario(500_000, base_shares=0, remainder=1),)  # some very high-demand scenario

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
        subscription_capital_limit=SubscriptionCapitalLimit(1_000_000.0),
    )

    executable = economics.allocations[0].executable
    assert executable is not None
    assert executable.shares == 0
    assert executable.capital_constrained is False  # capital wasn't the binding constraint, the mechanism was


def test_no_capital_limit_supplied_means_no_executable_allocation():
    scenarios = (_computed_scenario(50_000, base_shares=35, remainder=0),)

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
    )

    assert economics.allocations[0].executable is None


def test_capital_limit_supplied_but_offer_price_unresolved_means_no_executable_allocation():
    scenarios = (_computed_scenario(50_000, base_shares=35, remainder=0),)

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=None,
        subscription_capital_limit=SubscriptionCapitalLimit(5000.0),
    )

    assert economics.allocations[0].executable is None
    # theoretical capital/stress P&L are still reported off the theoretical baseline
    assert economics.allocations[0].theoretical_capital_tl == 35 * OFFER_PRICE
    assert economics.allocations[0].stress_outcomes


def test_non_standard_scenario_count_falls_back_to_generic_participant_count_labels():
    scenarios = (_computed_scenario(123_456, base_shares=1, remainder=0),)

    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None, offer_price=OFFER_PRICE,
    )

    assert "123.456" in economics.allocations[0].demand_label
