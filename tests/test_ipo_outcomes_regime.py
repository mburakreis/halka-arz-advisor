from datetime import date, datetime, timedelta

from halka_arz_advisor.ipo_outcomes.models import IpoMarketOutcome
from halka_arz_advisor.ipo_outcomes.regime import (
    MIN_MATURE_IPOS_FOR_REGIME,
    build_recent_ipo_regime,
)

AS_OF = datetime(2026, 8, 10)


def _outcome(ticker: str, trading_start: date | None, bist_relative_5d: float | None) -> IpoMarketOutcome:
    return IpoMarketOutcome(
        ticker=ticker, company_name=None, offer_price=10.0,
        resolved_trading_start_date=trading_start, spk_trading_start_date=trading_start,
        kap_trading_start_announcement_dates=(), trading_start_conflict=False,
        price_observation_count=10, last_price_observation_date=trading_start,
        first_day_return=5.0, return_5d=8.0, return_20d=None, return_3m=None,
        max_drawdown_5d=None, max_drawdown_20d=None, max_drawdown_3m=None,
        bist_relative_first_day=5.0, bist_relative_5d=bist_relative_5d, bist_relative_20d=None, bist_relative_3m=None,
        generated_at=datetime(2026, 7, 1),
    )


def test_too_few_mature_ipos_reports_unknown_not_a_guess():
    outcomes = [_outcome("A", date(2026, 7, 1), 10.0), _outcome("B", date(2026, 7, 5), -5.0)]
    assert len(outcomes) < MIN_MATURE_IPOS_FOR_REGIME

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert regime.status == "UNKNOWN"
    assert regime.median_bist_relative_return_5d is None
    assert regime.positive_bist_relative_share_5d is None


def test_favorable_regime_from_a_clear_majority_positive():
    outcomes = [_outcome(f"T{i}", date(2026, 7, 1), 20.0) for i in range(5)]

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert regime.status == "FAVORABLE"
    assert regime.mature_ipo_count == 5
    assert regime.positive_bist_relative_share_5d == 1.0
    assert regime.median_bist_relative_return_5d == 20.0


def test_unfavorable_regime_from_a_clear_majority_negative():
    outcomes = [_outcome(f"T{i}", date(2026, 7, 1), -15.0) for i in range(5)]

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert regime.status == "UNFAVORABLE"


def test_neutral_regime_from_a_mixed_split():
    outcomes = [_outcome(f"T{i}", date(2026, 7, 1), 10.0 if i < 3 else -10.0) for i in range(6)]

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert regime.status == "NEUTRAL"


def test_target_ipo_own_outcome_is_never_used_even_if_it_would_otherwise_qualify():
    # TARGET traded well within the lookback window and its own return
    # window is fully realized before as_of — it would pass every
    # maturity check on its own, but must still never be counted.
    outcomes = [_outcome("TARGET", date(2026, 7, 1), 999.0)] + [
        _outcome(f"T{i}", date(2026, 7, 1), 10.0) for i in range(3)
    ]

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker="TARGET")

    assert "TARGET" not in regime.included_tickers
    assert regime.mature_ipo_count == 3
    assert regime.median_bist_relative_return_5d == 10.0  # not skewed by the excluded 999.0


def test_exclude_ticker_is_case_insensitive():
    outcomes = [_outcome("target", date(2026, 7, 1), 999.0)] + [_outcome(f"T{i}", date(2026, 7, 1), 10.0) for i in range(3)]

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker="TARGET")

    assert regime.mature_ipo_count == 3


def test_ipo_that_started_trading_after_as_of_is_excluded():
    outcomes = [_outcome(f"T{i}", date(2026, 9, 1), 10.0) for i in range(5)]  # after AS_OF

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert regime.status == "UNKNOWN"
    assert regime.mature_ipo_count == 0


def test_ipo_whose_5d_window_has_not_yet_completed_is_excluded():
    # Started trading only 3 calendar days before as_of — 5 trading
    # days cannot have elapsed yet, so this must not be counted even
    # though bist_relative_5d happens to already have a value cached.
    outcomes = [_outcome(f"T{i}", AS_OF.date() - timedelta(days=3), 10.0) for i in range(5)]

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert regime.mature_ipo_count == 0


def test_ipo_outside_the_lookback_window_is_excluded():
    outcomes = [_outcome(f"T{i}", date(2026, 1, 1), 10.0) for i in range(5)]  # far more than 90 days before AS_OF

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None, lookback_days=90)

    assert regime.mature_ipo_count == 0


def test_ipo_with_no_bist_relative_5d_value_is_excluded():
    outcomes = [_outcome(f"T{i}", date(2026, 7, 1), None) for i in range(5)]

    regime = build_recent_ipo_regime(outcomes, as_of=AS_OF, exclude_ticker=None)

    assert regime.mature_ipo_count == 0
