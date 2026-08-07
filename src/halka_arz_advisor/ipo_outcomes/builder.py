"""Orchestrates one ticker's :class:`~halka_arz_advisor.ipo_outcomes.models.IpoMarketOutcome`:
resolve the trading-start date (:mod:`halka_arz_advisor.ipo_outcomes.trading_start`),
pull that ticker's price history from :mod:`halka_arz_advisor.market_prices`
(cached-first, fetching only what's genuinely missing), and compute
every deterministic field from :mod:`halka_arz_advisor.ipo_outcomes.calculations`
against it and the already-cached EVDS BIST 100 series — this module is
the only place in the package that touches the network (transitively,
through :func:`halka_arz_advisor.market_prices.provider.get_observations`)
or decides how far forward to look.

Never imports :mod:`halka_arz_advisor.decision` or anything Gemini/
Telegram — this package's output is read-only, backtest-oriented data,
not a scoring input.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from ..evds.models import EvdsObservation
from ..kap.models import KapDisclosure
from ..market_prices.cache import BulletinCache
from ..market_prices.config import MarketPricesConfig
from ..market_prices.provider import get_observations
from ..probe.config import ProbeConfig
from ..spk.models import SpkIpoRecord
from .calculations import (
    RETURN_WINDOWS,
    bist_relative_return,
    first_day_return,
    n_day_max_drawdown,
    n_day_return,
)
from .models import IpoMarketOutcome
from .trading_start import resolve_trading_start_date

# Calendar-day lookahead used to bound the price-history fetch window.
# Comfortably covers the 60-trading-observation ("3m") window plus
# weekends and Turkish public holidays with margin — mirrors
# halka_arz_advisor.evds.refresh's own bounded-lookback convention
# (never scan unlimited history, but never let a holiday cluster starve
# a window either).
_LOOKAHEAD_CALENDAR_DAYS = 100


def build_ipo_market_outcome(
    ticker: str,
    *,
    ipo_record: SpkIpoRecord | None,
    disclosures: Sequence[KapDisclosure],
    company_name: str | None,
    bulletin_cache: BulletinCache,
    bist100_observations: Sequence[EvdsObservation],
    reference_date: date | None = None,
    market_prices_config: MarketPricesConfig | None = None,
    probe_config: ProbeConfig | None = None,
) -> IpoMarketOutcome:
    """Build (never mutates any existing stored record — that's
    :meth:`halka_arz_advisor.ipo_outcomes.store.IpoMarketOutcomeStore.put`'s
    job) one ticker's outcome.

    Every return/drawdown field is anchored at ``ipo_record``'s own
    ``halka_arz_fiyati_tl`` — the canonical official offer price this
    project already surfaces elsewhere (see
    :mod:`halka_arz_advisor.notify.formatting`) — never a second,
    independently-derived price. If the trading-start date can't be
    resolved, or ``ipo_record``/its offer price is missing, every metric
    field is honestly ``None`` rather than guessed.
    """
    ref = reference_date or datetime.now(UTC).date()
    generated_at = datetime.now(UTC)
    resolution = resolve_trading_start_date(ticker, ipo_record, disclosures)
    offer_price = ipo_record.halka_arz_fiyati_tl if ipo_record is not None else None

    if resolution.resolved_date is None:
        return IpoMarketOutcome(
            ticker=ticker,
            company_name=company_name,
            offer_price=offer_price,
            resolved_trading_start_date=None,
            spk_trading_start_date=resolution.spk_trading_start_date,
            kap_trading_start_announcement_dates=resolution.kap_trading_start_announcement_dates,
            trading_start_conflict=resolution.conflict,
            price_observation_count=0,
            last_price_observation_date=None,
            first_day_return=None, return_5d=None, return_20d=None, return_3m=None,
            max_drawdown_5d=None, max_drawdown_20d=None, max_drawdown_3m=None,
            bist_relative_first_day=None, bist_relative_5d=None, bist_relative_20d=None, bist_relative_3m=None,
            generated_at=generated_at,
        )

    start = resolution.resolved_date
    window_end = min(start + timedelta(days=_LOOKAHEAD_CALENDAR_DAYS), ref)
    price_observations = (
        get_observations(
            ticker, start, window_end, cache=bulletin_cache, config=market_prices_config, probe_config=probe_config
        )
        if window_end >= start
        else ()
    )
    ordered = sorted(price_observations, key=lambda o: o.trading_date)

    fd = first_day_return(ordered, offer_price)
    n5 = n_day_return(ordered, offer_price, RETURN_WINDOWS["5d"])
    n20 = n_day_return(ordered, offer_price, RETURN_WINDOWS["20d"])
    n3m = n_day_return(ordered, offer_price, RETURN_WINDOWS["3m"])
    dd5 = n_day_max_drawdown(ordered, offer_price, RETURN_WINDOWS["5d"])
    dd20 = n_day_max_drawdown(ordered, offer_price, RETURN_WINDOWS["20d"])
    dd3m = n_day_max_drawdown(ordered, offer_price, RETURN_WINDOWS["3m"])

    brfd = bist_relative_return(fd, bist100_observations, start)
    br5 = bist_relative_return(n5, bist100_observations, start)
    br20 = bist_relative_return(n20, bist100_observations, start)
    br3m = bist_relative_return(n3m, bist100_observations, start)

    return IpoMarketOutcome(
        ticker=ticker,
        company_name=company_name,
        offer_price=offer_price,
        resolved_trading_start_date=start,
        spk_trading_start_date=resolution.spk_trading_start_date,
        kap_trading_start_announcement_dates=resolution.kap_trading_start_announcement_dates,
        trading_start_conflict=resolution.conflict,
        price_observation_count=len(ordered),
        last_price_observation_date=ordered[-1].trading_date if ordered else None,
        first_day_return=fd.value if fd is not None else None,
        return_5d=n5.value if n5 is not None else None,
        return_20d=n20.value if n20 is not None else None,
        return_3m=n3m.value if n3m is not None else None,
        max_drawdown_5d=dd5.value if dd5 is not None else None,
        max_drawdown_20d=dd20.value if dd20 is not None else None,
        max_drawdown_3m=dd3m.value if dd3m is not None else None,
        bist_relative_first_day=brfd.value if brfd is not None else None,
        bist_relative_5d=br5.value if br5 is not None else None,
        bist_relative_20d=br20.value if br20 is not None else None,
        bist_relative_3m=br3m.value if br3m is not None else None,
        generated_at=generated_at,
    )
