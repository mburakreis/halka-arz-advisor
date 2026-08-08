from datetime import UTC, date, datetime

import pytest

from halka_arz_advisor.evds.cache import EvdsCache
from halka_arz_advisor.evds.config import load_evds_config_from_env
from halka_arz_advisor.evds.features import bist100_max_drawdown, bist100_return, bist100_volatility, build_market_context_snapshot
from halka_arz_advisor.evds.models import EvdsObservation
from halka_arz_advisor.evds.parsing import parse_evds_items
from halka_arz_advisor.evds.refresh import refresh_market_context
from halka_arz_advisor.evds.registry import get_series_spec

FETCHED_AT = datetime(2026, 8, 7, tzinfo=UTC)


def _obs(observation_date: date, value: float) -> EvdsObservation:
    return EvdsObservation(
        series_code="TP.MK.F.BILESIK", observation_date=observation_date, value=value,
        unit="index_points", frequency="daily", source_institution="Borsa İstanbul", fetched_at=FETCHED_AT,
    )


# --------------------------------------------------------------------------
# 1. Parsed EVDS response
# --------------------------------------------------------------------------


def test_parse_evds_items_skips_nulls_and_maps_daily_and_monthly_dates():
    daily_specs = [get_series_spec("bist100_index"), get_series_spec("bist100_volume")]
    daily_items = [
        # A date the volume column hasn't published yet (EVDS's own
        # null-before-publication convention) — must be skipped for
        # bist100_volume only, not fabricated as 0.0, and must not drop
        # the bist100_index observation for the same date.
        {"Tarih": "05-08-2026", "TP_MK_F_BILESIK": "13703.13000000", "TP_MK_ISL_HC": None},
        {"Tarih": "06-08-2026", "TP_MK_F_BILESIK": "13798.82000000", "TP_MK_ISL_HC": "264591164.39338000"},
    ]
    result = parse_evds_items(daily_items, daily_specs, fetched_at=FETCHED_AT)

    assert [o.observation_date for o in result["bist100_index"]] == [date(2026, 8, 5), date(2026, 8, 6)]
    assert result["bist100_index"][0].value == 13703.13
    assert result["bist100_index"][0].source_institution == "Borsa İstanbul"
    assert [o.observation_date for o in result["bist100_volume"]] == [date(2026, 8, 6)]

    monthly_spec = [get_series_spec("cpi_index")]
    monthly_items = [{"Tarih": "2026-7", "TP_TUKFIY2025_GENEL": "132.31000000"}]
    monthly_result = parse_evds_items(monthly_items, monthly_spec, fetched_at=FETCHED_AT)
    assert monthly_result["cpi_index"] == [
        EvdsObservation(
            series_code="TP.TUKFIY2025.GENEL", observation_date=date(2026, 7, 1), value=132.31,
            unit="index_points", frequency="monthly", source_institution="TÜİK", fetched_at=FETCHED_AT,
        ),
    ]


# --------------------------------------------------------------------------
# 2. Deterministic return/volatility/drawdown calculation
# --------------------------------------------------------------------------


def test_bist100_return_volatility_and_drawdown_are_computed_from_trading_observations_only():
    # A flat run then one final jump — trading-observation-counted, so a
    # gap in calendar dates (here: skipping straight from day 1 to day
    # 10) must not change the window's meaning; only the 5 observations
    # themselves count.
    flat_then_jump = [
        _obs(date(2026, 1, 1), 100.0),
        _obs(date(2026, 1, 10), 100.0),
        _obs(date(2026, 1, 11), 100.0),
        _obs(date(2026, 1, 12), 100.0),
        _obs(date(2026, 1, 13), 110.0),
    ]
    ret = bist100_return(flat_then_jump, 4)
    assert ret is not None
    assert ret.value == pytest.approx(10.0)
    assert ret.as_of_date == date(2026, 1, 13)

    # Not enough observations yet for a 20-observation window.
    assert bist100_return(flat_then_jump, 20) is None

    zero_volatility = bist100_volatility(flat_then_jump[:4], 3)  # three flat 100.0 observations
    assert zero_volatility is not None
    assert zero_volatility.value == pytest.approx(0.0)

    peak_then_trough = [
        _obs(date(2026, 2, 1), 100.0),
        _obs(date(2026, 2, 2), 120.0),
        _obs(date(2026, 2, 3), 80.0),
        _obs(date(2026, 2, 4), 90.0),
    ]
    drawdown = bist100_max_drawdown(peak_then_trough, 4)
    assert drawdown is not None
    # Peak 120 -> trough 80 is the worst decline: (80/120 - 1) * 100.
    assert drawdown.value == pytest.approx(-33.333333, rel=1e-6)


def test_snapshot_exposes_bist_index_level_from_the_same_bist100_index_series():
    # decision.catalog's broader_index_level_at_offer (market_data.bist_index_level)
    # reads this key — it must be the plain latest cached level, not a
    # window-relative return like the other bist100_* features.
    snapshot = build_market_context_snapshot(
        bist100_index=[_obs(date(2026, 1, 1), 100.0), _obs(date(2026, 1, 2), 105.0)],
        policy_rate_observations=[], tlref_observations=[], cpi_observations=[],
    )
    level = snapshot.get("bist_index_level")
    assert level is not None
    assert level.value == pytest.approx(105.0)
    assert level.as_of_date == date(2026, 1, 2)


# --------------------------------------------------------------------------
# 3. Unavailable provider (no API key)
# --------------------------------------------------------------------------


def test_missing_api_key_is_handled_gracefully_everywhere(tmp_path, monkeypatch):
    monkeypatch.delenv("EVDS_API_KEY", raising=False)
    assert load_evds_config_from_env() is None

    cache = EvdsCache(tmp_path / "evds")
    outcome = refresh_market_context(cache, config=None)

    assert outcome.skipped_no_key is True
    assert outcome.refreshed_series_keys == []
    assert outcome.failed_series_keys == {}
    # Nothing was ever cached — a downstream reader (e.g.
    # scripts/audit_decision_coverage.py) just sees an empty cache, not
    # an error.
    assert cache.get_observations("bist100_index") == ()
