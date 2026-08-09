from datetime import date, datetime

from halka_arz_advisor.decision.subscription_economics import (
    DEMAND_LABELS_ASCENDING,
    PersonalCapitalContext,
    build_return_scenarios,
    build_subscription_economics,
)
from halka_arz_advisor.ipo_outcomes.models import IpoMarketOutcome
from halka_arz_advisor.ipo_outcomes.regime import STRONG_EVIDENCE_MATURE_IPO_COUNT
from halka_arz_advisor.kap.allocation_scenario import AllocationScenario

AS_OF = datetime(2026, 8, 10)


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


def _computed_scenario(participant_count: int, base_shares: int, remainder: int, offer_price: float = 45.0) -> AllocationScenario:
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
# build_return_scenarios
# --------------------------------------------------------------------------


def test_too_few_mature_comparables_falls_back_to_illustrative_scenarios():
    outcomes = [_outcome(f"T{i}", 10.0) for i in range(STRONG_EVIDENCE_MATURE_IPO_COUNT - 1)]

    scenarios, source = build_return_scenarios(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert source == "illustrative"
    assert all(s.source == "illustrative" for s in scenarios)
    assert any("gösterge" in s.label for s in scenarios)


def test_enough_mature_comparables_grounds_scenarios_in_real_worst_median_best():
    values = [-20.0, -5.0, 10.0, 20.0, 30.0, 60.0]
    outcomes = [_outcome(f"T{i}", v) for i, v in enumerate(values)]
    assert len(outcomes) == STRONG_EVIDENCE_MATURE_IPO_COUNT

    scenarios, source = build_return_scenarios(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert source == "historical_regime"
    assert all(s.source == "historical_regime" for s in scenarios)
    pcts = [s.return_pct for s in scenarios]
    assert pcts == [-0.20, 0.15, 0.60]  # worst / median(10, 20) = 15 / best, as fractions of the raw % values


def test_target_ipos_own_outcome_never_counted_in_return_scenarios():
    values = [-20.0, -5.0, 10.0, 20.0, 30.0, 60.0]
    outcomes = [_outcome("TARGET", 9999.0)] + [_outcome(f"T{i}", v) for i, v in enumerate(values)]

    scenarios, source = build_return_scenarios(outcomes, as_of=AS_OF, exclude_ticker="TARGET")

    assert source == "historical_regime"
    assert max(s.return_pct for s in scenarios) == 0.60  # not skewed by TARGET's own 9999.0


def test_outcomes_missing_return_5d_do_not_count_toward_the_evidence_bar():
    outcomes = [_outcome(f"T{i}", None) for i in range(STRONG_EVIDENCE_MATURE_IPO_COUNT)]

    scenarios, source = build_return_scenarios(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert source == "illustrative"


# --------------------------------------------------------------------------
# build_subscription_economics
# --------------------------------------------------------------------------


def test_three_scenarios_get_ascending_demand_labels_and_per_scenario_capital():
    scenarios = (
        _computed_scenario(50_000, base_shares=35, remainder=0),
        _computed_scenario(200_000, base_shares=14, remainder=0),
        _computed_scenario(500_000, base_shares=6, remainder=0),
    )
    outcomes = [_outcome(f"T{i}", v) for i, v in enumerate([-20.0, -5.0, 10.0, 20.0, 30.0, 60.0])]

    economics = build_subscription_economics(scenarios, recent_ipo_outcomes=outcomes, as_of=AS_OF, exclude_ticker=None)

    assert [a.demand_label for a in economics.allocations] == list(DEMAND_LABELS_ASCENDING)
    low, typical, high = economics.allocations
    assert low.capital_tl == 35 * 45.0
    assert typical.capital_tl == 14 * 45.0
    assert high.capital_tl == 6 * 45.0
    # each allocation's own P&L is computed on its own capital, not a shared/blended figure
    for allocation in economics.allocations:
        for outcome in allocation.return_outcomes:
            assert outcome.profit_loss_tl == allocation.capital_tl * outcome.scenario.return_pct


def test_unavailable_allocation_scenario_yields_no_return_outcomes_not_a_guess():
    scenarios = (_unavailable_scenario(50_000),)
    economics = build_subscription_economics(scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None)

    assert economics.allocations[0].capital_tl is None
    assert economics.allocations[0].return_outcomes == ()


def test_personal_capital_flags_a_scenario_that_exceeds_available_capital():
    scenarios = (_computed_scenario(500_000, base_shares=6, remainder=0),)  # 6 * 45.0 = 270 TL
    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None,
        personal_capital=PersonalCapitalContext(available_capital_tl=100.0),
    )

    assert economics.personal_capital_notes
    assert "aşıyor" in economics.personal_capital_notes[0]


def test_personal_capital_reports_the_capital_share_when_affordable():
    scenarios = (_computed_scenario(500_000, base_shares=6, remainder=0),)  # 270 TL
    economics = build_subscription_economics(
        scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None,
        personal_capital=PersonalCapitalContext(available_capital_tl=2700.0),
    )

    assert economics.personal_capital_notes
    assert "%10.0" in economics.personal_capital_notes[0]


def test_no_personal_capital_input_means_no_notes():
    scenarios = (_computed_scenario(50_000, base_shares=35, remainder=0),)
    economics = build_subscription_economics(scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None)

    assert economics.personal_capital_notes == ()


def test_non_standard_scenario_count_falls_back_to_generic_participant_count_labels():
    scenarios = (_computed_scenario(123_456, base_shares=1, remainder=0),)
    economics = build_subscription_economics(scenarios, recent_ipo_outcomes=(), as_of=AS_OF, exclude_ticker=None)

    assert "123.456" in economics.allocations[0].demand_label
