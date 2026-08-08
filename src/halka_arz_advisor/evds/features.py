"""Deterministic derivation of the ten market-context features this
project exposes to the decision coverage audit — pure arithmetic over
already-cached :class:`~halka_arz_advisor.evds.models.EvdsObservation`
sequences, no scoring/weighting/normalization and no network access.

BIST return/volatility/drawdown windows are always counted in *trading
observations*, never calendar days — a market holiday, an EVDS
publishing gap, or any other missing business day must not silently
shift what "20 days" means. A feature is only ever produced when there
are genuinely enough cached observations to compute it; otherwise
nothing is added for that feature (never a fabricated/zero-filled
value) — see :func:`build_market_context_snapshot`.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import EvdsObservation, MarketContextFeatureValue, MarketContextSnapshot

# feature name -> trailing trading-observation window width
_RETURN_WINDOWS: dict[str, int] = {
    "bist100_return_20d": 20,
    "bist100_return_60d": 60,
    "bist100_return_120d": 120,
}
_VOLATILITY_WINDOWS: dict[str, int] = {"bist100_volatility_20d": 20}
_DRAWDOWN_WINDOWS: dict[str, int] = {"bist100_max_drawdown_60d": 60}


def _sorted_observations(observations: Sequence[EvdsObservation]) -> list[EvdsObservation]:
    return sorted(observations, key=lambda o: o.observation_date)


def bist100_return(observations: Sequence[EvdsObservation], trading_observations: int) -> MarketContextFeatureValue | None:
    """Percentage return from the start to the end of the trailing
    ``trading_observations``-observation window (i.e. ``trading_observations``
    *returns*, so ``trading_observations + 1`` price points are needed)."""
    ordered = _sorted_observations(observations)
    if len(ordered) < trading_observations + 1:
        return None
    start, end = ordered[-(trading_observations + 1)], ordered[-1]
    if start.value == 0:
        return None
    pct_return = (end.value / start.value - 1.0) * 100.0
    return MarketContextFeatureValue(value=pct_return, as_of_date=end.observation_date, source_series_codes=(end.series_code,))


def bist100_volatility(observations: Sequence[EvdsObservation], trading_observations: int) -> MarketContextFeatureValue | None:
    """Sample standard deviation (percent) of day-over-day simple
    returns across the trailing ``trading_observations`` window."""
    ordered = _sorted_observations(observations)
    if len(ordered) < trading_observations + 1:
        return None
    window = ordered[-(trading_observations + 1):]
    daily_returns: list[float] = []
    for prev, curr in zip(window, window[1:]):
        if prev.value == 0:
            return None
        daily_returns.append(curr.value / prev.value - 1.0)
    if len(daily_returns) < 2:
        return None
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    stdev_pct = (variance**0.5) * 100.0
    return MarketContextFeatureValue(value=stdev_pct, as_of_date=window[-1].observation_date, source_series_codes=(window[-1].series_code,))


def bist100_max_drawdown(observations: Sequence[EvdsObservation], trading_observations: int) -> MarketContextFeatureValue | None:
    """Largest peak-to-trough decline (percent, ``<= 0``) within the
    trailing ``trading_observations`` window."""
    ordered = _sorted_observations(observations)
    if len(ordered) < trading_observations:
        return None
    window = ordered[-trading_observations:]
    running_max = window[0].value
    max_drawdown = 0.0
    for obs in window:
        running_max = max(running_max, obs.value)
        if running_max > 0:
            drawdown = (obs.value / running_max - 1.0) * 100.0
            max_drawdown = min(max_drawdown, drawdown)
    return MarketContextFeatureValue(value=max_drawdown, as_of_date=window[-1].observation_date, source_series_codes=(window[-1].series_code,))


def latest_value(observations: Sequence[EvdsObservation]) -> MarketContextFeatureValue | None:
    """The most recent cached observation's value, unchanged — used for
    ``policy_rate``/``tlref_rate``, which are already rates, not levels
    to derive a return from."""
    ordered = _sorted_observations(observations)
    if not ordered:
        return None
    latest = ordered[-1]
    return MarketContextFeatureValue(value=latest.value, as_of_date=latest.observation_date, source_series_codes=(latest.series_code,))


def cpi_yoy(observations: Sequence[EvdsObservation]) -> MarketContextFeatureValue | None:
    """Year-over-year percentage change of the latest cached CPI index
    observation against the observation from the same calendar month
    one year earlier — matched by calendar month, never by a fixed
    observation-count offset (a monthly series has no "trading
    observation" concept, and a missed/late publication must not shift
    which prior month is compared)."""
    ordered = _sorted_observations(observations)
    if not ordered:
        return None
    latest = ordered[-1]
    target_year = latest.observation_date.year - 1
    target_month = latest.observation_date.month
    prior = next(
        (o for o in ordered if o.observation_date.year == target_year and o.observation_date.month == target_month), None
    )
    if prior is None or prior.value == 0:
        return None
    pct = (latest.value / prior.value - 1.0) * 100.0
    return MarketContextFeatureValue(value=pct, as_of_date=latest.observation_date, source_series_codes=(latest.series_code,))


def policy_rate_minus_cpi(
    policy_rate: MarketContextFeatureValue | None, cpi_yoy_value: MarketContextFeatureValue | None
) -> MarketContextFeatureValue | None:
    """The (approximate) real policy rate: ``policy_rate - cpi_yoy``,
    both already in percentage points. Only produced when both inputs
    are themselves available."""
    if policy_rate is None or cpi_yoy_value is None:
        return None
    value = policy_rate.value - cpi_yoy_value.value
    as_of_date = max(policy_rate.as_of_date, cpi_yoy_value.as_of_date)
    codes = tuple(sorted(set(policy_rate.source_series_codes) | set(cpi_yoy_value.source_series_codes)))
    return MarketContextFeatureValue(value=value, as_of_date=as_of_date, source_series_codes=codes)


def build_market_context_snapshot(
    *,
    bist100_index: Sequence[EvdsObservation],
    policy_rate_observations: Sequence[EvdsObservation],
    tlref_observations: Sequence[EvdsObservation],
    cpi_observations: Sequence[EvdsObservation],
) -> MarketContextSnapshot:
    """Compute every one of the ten market-context features this
    project currently exposes — see this module's docstring for why a
    feature is simply absent from the result rather than fabricated
    when there isn't enough cached data yet."""
    features: dict[str, MarketContextFeatureValue] = {}

    # The same bist100_index series already used for the return/
    # volatility/drawdown windows below, just read as a raw level
    # instead of a window-relative change — feeds
    # decision.catalog's broader_index_level_at_offer
    # (market_data.bist_index_level), not a second series or a new
    # extractor.
    index_level_value = latest_value(bist100_index)
    if index_level_value is not None:
        features["bist_index_level"] = index_level_value

    for name, window in _RETURN_WINDOWS.items():
        value = bist100_return(bist100_index, window)
        if value is not None:
            features[name] = value

    for name, window in _VOLATILITY_WINDOWS.items():
        value = bist100_volatility(bist100_index, window)
        if value is not None:
            features[name] = value

    for name, window in _DRAWDOWN_WINDOWS.items():
        value = bist100_max_drawdown(bist100_index, window)
        if value is not None:
            features[name] = value

    policy_rate_value = latest_value(policy_rate_observations)
    if policy_rate_value is not None:
        features["policy_rate"] = policy_rate_value

    tlref_value = latest_value(tlref_observations)
    if tlref_value is not None:
        features["tlref_rate"] = tlref_value

    cpi_yoy_value = cpi_yoy(cpi_observations)
    if cpi_yoy_value is not None:
        features["cpi_yoy"] = cpi_yoy_value

    spread = policy_rate_minus_cpi(policy_rate_value, cpi_yoy_value)
    if spread is not None:
        features["policy_rate_minus_cpi"] = spread

    return MarketContextSnapshot(features=features)
