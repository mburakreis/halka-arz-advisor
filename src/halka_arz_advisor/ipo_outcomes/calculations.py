"""Deterministic IPO market-outcome arithmetic — pure functions over
already-fetched :class:`~halka_arz_advisor.market_prices.models.DailyPriceObservation`
and :class:`~halka_arz_advisor.evds.models.EvdsObservation` sequences.
No network access, no scoring/weighting, and nothing here ever feeds
:mod:`halka_arz_advisor.decision`.

Every outcome measures the return an investor who was actually
**allotted shares in the IPO** experienced — always anchored at the
IPO's own official offer price (:attr:`halka_arz_advisor.spk.models.SpkIpoRecord.halka_arz_fiyati_tl`,
the canonical field this project already uses elsewhere, e.g.
:mod:`halka_arz_advisor.notify.formatting`), never at the first trading
day's own open or close. Concretely, with D1/D5/D20/D60 meaning the
close on the 1st/5th/20th/60th trading session after listing:

* ``first_day_return`` = D1 close / offer price - 1
* ``return_5d`` = D5 close / offer price - 1
* ``return_20d`` = D20 close / offer price - 1
* ``return_3m`` = D60 close / offer price - 1

so these need *exactly* 1/5/20/60 trading-session observations — never
``N + 1`` — to exist (mirroring, but not reusing, the "count trading
observations, never calendar days" discipline of
:mod:`halka_arz_advisor.evds.features`; the 5/20/60 widths reuse this
project's own already-established 1-month/1-quarter trading-day
convention, see ``evds.features._RETURN_WINDOWS``'s ``20d``/``60d``/
``120d`` keys — ``return_3m`` uses 60 trading sessions, not a 3-calendar-
month date offset).

Each drawdown treats the offer price as the initial portfolio value —
the series ``[offer_price, D1 close, ..., DN close]`` — so
``max_drawdown_Nd`` can be as extreme as "the stock never traded above
its own offer price," which a close-to-close-only drawdown could never
show. A value is only ever produced when there are genuinely enough
observations for that full window; otherwise ``None`` (never
interpolated, extrapolated, or approximated from a partial window).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ..evds.models import EvdsObservation
from ..market_prices.models import DailyPriceObservation

# Trading session count, keyed by this project's outcome field name
# suffix — see this module's docstring for why 3m == 60. D1 (the
# first_day_return field) is just the trading_observations=1 case of
# the same n_day_* functions below.
RETURN_WINDOWS: dict[str, int] = {"1d": 1, "5d": 5, "20d": 20, "3m": 60}


@dataclass(frozen=True, slots=True)
class OutcomeValue:
    value: float
    as_of_date: date


def _sorted_by_date(observations: Sequence[DailyPriceObservation]) -> list[DailyPriceObservation]:
    return sorted(observations, key=lambda o: o.trading_date)


def n_day_return(
    observations: Sequence[DailyPriceObservation], offer_price: float | None, trading_observations: int
) -> OutcomeValue | None:
    """Percentage return from the IPO's own offer price to the close of
    the ``trading_observations``-th trading session after listing (D1
    for ``trading_observations=1``, i.e. ``first_day_return``) — needs
    ``offer_price`` and exactly that many trading-session observations,
    no more, no fewer."""
    if offer_price is None or offer_price == 0:
        return None
    ordered = _sorted_by_date(observations)
    if len(ordered) < trading_observations:
        return None
    day_n = ordered[trading_observations - 1]
    pct = (day_n.close / offer_price - 1.0) * 100.0
    return OutcomeValue(value=pct, as_of_date=day_n.trading_date)


def first_day_return(observations: Sequence[DailyPriceObservation], offer_price: float | None) -> OutcomeValue | None:
    """D1 close vs. the IPO's own offer price — see this module's
    docstring for why this is not an intraday open-to-close return."""
    return n_day_return(observations, offer_price, RETURN_WINDOWS["1d"])


def n_day_max_drawdown(
    observations: Sequence[DailyPriceObservation], offer_price: float | None, trading_observations: int
) -> OutcomeValue | None:
    """Largest peak-to-trough decline (percent, ``<= 0``) across
    ``[offer_price, D1 close, ..., D<trading_observations> close]`` —
    the offer price is the starting portfolio value, so a stock that
    never once traded back up to its own offer price shows the full
    decline, not just its post-listing volatility."""
    if offer_price is None or offer_price == 0:
        return None
    ordered = _sorted_by_date(observations)
    if len(ordered) < trading_observations:
        return None
    window = ordered[:trading_observations]
    running_max = offer_price
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


def bist_relative_return(
    ipo_return: OutcomeValue | None,
    bist100_observations: Sequence[EvdsObservation],
    listing_date: date,
) -> OutcomeValue | None:
    """``ipo_return`` minus BIST 100's own cumulative return over the
    identical span: from the last available BIST 100 close *before*
    ``listing_date`` (D1's own date — the same baseline used for every
    one of first_day/5d/20d/3m, mirroring the IPO side's own single
    offer-price anchor) through BIST 100's close on ``ipo_return``'s own
    ``as_of_date`` (D1/D5/D20/D60's date). Never fetches EVDS itself,
    and never falls back to a nearby date if either side is missing
    from the already-cached series — just ``None``."""
    if ipo_return is None:
        return None
    baseline = _bist100_previous_close(bist100_observations, listing_date)
    end_close = _bist100_close_on(bist100_observations, ipo_return.as_of_date)
    if baseline is None or end_close is None or baseline[1] == 0:
        return None
    bist_return = (end_close / baseline[1] - 1.0) * 100.0
    return OutcomeValue(value=ipo_return.value - bist_return, as_of_date=ipo_return.as_of_date)
