"""Deterministic IPO market-outcome arithmetic — pure functions over
already-fetched :class:`~halka_arz_advisor.market_prices.models.DailyPriceObservation`
and :class:`~halka_arz_advisor.evds.models.EvdsObservation` sequences.
No network access, no scoring/weighting, and nothing here ever feeds
:mod:`halka_arz_advisor.decision`.

Every return/drawdown window is counted in **trading observations**,
never calendar days — mirrors :mod:`halka_arz_advisor.evds.features`'s
own "a market holiday or gap must not silently shift what N days means"
rule, just forward-anchored at the IPO's own first trading day instead
of trailing from "now". The 5/20/60 window widths reuse this project's
own already-established 1-month/1-quarter trading-day convention (see
``evds.features._RETURN_WINDOWS``'s ``20d``/``60d``/``120d`` keys):
``return_3m``/``max_drawdown_3m`` use a 60-trading-observation window,
not a 3-calendar-month date offset, so a listing right before a cluster
of public holidays doesn't shrink what "3 months" means.

A window's return and its max-drawdown are computed over the *same*
set of trading days (``N + 1`` closes: the listing-day close through
the Nth-trading-day close) so ``return_Nd`` and ``max_drawdown_Nd``
always describe the identical period — a value is only ever produced
when there are genuinely enough observations for that full window;
otherwise ``None`` (never interpolated or approximated).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ..evds.models import EvdsObservation
from ..market_prices.models import DailyPriceObservation

# trading-observation window width, keyed by this project's outcome
# field name suffix — see this module's docstring for why 3m == 60.
RETURN_WINDOWS: dict[str, int] = {"5d": 5, "20d": 20, "3m": 60}


@dataclass(frozen=True, slots=True)
class OutcomeValue:
    value: float
    as_of_date: date


def _sorted_by_date(observations: Sequence[DailyPriceObservation]) -> list[DailyPriceObservation]:
    return sorted(observations, key=lambda o: o.trading_date)


def first_day_return(observations: Sequence[DailyPriceObservation]) -> OutcomeValue | None:
    """Intraday open-to-close return on the IPO's own first trading day
    — the one outcome field that doesn't need a second trading day of
    data to exist."""
    ordered = _sorted_by_date(observations)
    if not ordered:
        return None
    day0 = ordered[0]
    if day0.open == 0:
        return None
    pct = (day0.close / day0.open - 1.0) * 100.0
    return OutcomeValue(value=pct, as_of_date=day0.trading_date)


def n_day_return(observations: Sequence[DailyPriceObservation], trading_observations: int) -> OutcomeValue | None:
    """Close-to-close percentage return from the listing day's close to
    the close ``trading_observations`` trading days later (so
    ``trading_observations + 1`` price points, starting at the listing
    day, are required)."""
    ordered = _sorted_by_date(observations)
    if len(ordered) < trading_observations + 1:
        return None
    day0, dayN = ordered[0], ordered[trading_observations]
    if day0.close == 0:
        return None
    pct = (dayN.close / day0.close - 1.0) * 100.0
    return OutcomeValue(value=pct, as_of_date=dayN.trading_date)


def n_day_max_drawdown(observations: Sequence[DailyPriceObservation], trading_observations: int) -> OutcomeValue | None:
    """Largest peak-to-trough decline (percent, ``<= 0``) in closing
    price across the same ``[listing day, listing day + trading_observations]``
    window ``n_day_return`` uses for the same ``trading_observations``."""
    ordered = _sorted_by_date(observations)
    if len(ordered) < trading_observations + 1:
        return None
    window = ordered[: trading_observations + 1]
    running_max = window[0].close
    max_drawdown = 0.0
    for obs in window:
        running_max = max(running_max, obs.close)
        if running_max > 0:
            drawdown = (obs.close / running_max - 1.0) * 100.0
            max_drawdown = min(max_drawdown, drawdown)
    return OutcomeValue(value=max_drawdown, as_of_date=window[-1].trading_date)


def _bist100_close_on(observations: Sequence[EvdsObservation], target: date) -> float | None:
    return next((o.value for o in observations if o.observation_date == target), None)


def _bist100_previous_close(observations: Sequence[EvdsObservation], before: date) -> tuple[date, float] | None:
    """Latest cached BIST 100 observation strictly before ``before`` —
    the exchange's own previous trading day, read off whatever's
    already cached rather than assumed from a calendar."""
    earlier = [o for o in observations if o.observation_date < before]
    if not earlier:
        return None
    latest = max(earlier, key=lambda o: o.observation_date)
    return latest.observation_date, latest.value


def bist_relative_first_day(
    ipo_first_day_return: OutcomeValue | None, bist100_observations: Sequence[EvdsObservation], listing_date: date
) -> OutcomeValue | None:
    """``first_day_return`` minus BIST 100's own close-to-close change
    on the same listing date (previous cached close -> listing-date
    close) — both are day-over-day moves, even though the IPO side is
    intraday (open->close) and the index side is interday (prior
    close->close): the two are the standard, if imperfectly matched,
    "did this IPO outperform the market that same day" comparison, and
    is never computed if either observation is missing rather than
    approximated with a nearby date."""
    if ipo_first_day_return is None:
        return None
    listing_close = _bist100_close_on(bist100_observations, listing_date)
    previous = _bist100_previous_close(bist100_observations, listing_date)
    if listing_close is None or previous is None or previous[1] == 0:
        return None
    bist_change = (listing_close / previous[1] - 1.0) * 100.0
    return OutcomeValue(value=ipo_first_day_return.value - bist_change, as_of_date=listing_date)


def bist_relative_return(
    ipo_return: OutcomeValue | None,
    bist100_observations: Sequence[EvdsObservation],
    start_date: date,
    end_date: date,
) -> OutcomeValue | None:
    """``ipo_return`` minus BIST 100's own return over the identical
    ``[start_date, end_date]`` calendar span (both are Borsa İstanbul
    trading days for the same instrument's own listing/session dates,
    so an exact-date match is expected — never fetches EVDS itself, and
    never falls back to a nearby date if one side is missing from the
    already-cached series)."""
    if ipo_return is None:
        return None
    start_close = _bist100_close_on(bist100_observations, start_date)
    end_close = _bist100_close_on(bist100_observations, end_date)
    if start_close is None or end_close is None or start_close == 0:
        return None
    bist_return = (end_close / start_close - 1.0) * 100.0
    return OutcomeValue(value=ipo_return.value - bist_return, as_of_date=end_date)
