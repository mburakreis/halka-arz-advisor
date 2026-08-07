"""Public entry point for retrieving one ticker's official daily price
history — the only function :mod:`halka_arz_advisor.ipo_outcomes` (or
any other caller) should use; everything else in this package is an
implementation detail of how a day gets fetched, parsed, and cached.

Walks every **calendar** date in ``[start_date, end_date]``, using
:class:`~halka_arz_advisor.market_prices.cache.BulletinCache` to avoid
ever re-requesting a date this project has already resolved (available
or confirmed-unavailable) — so two IPOs whose return-window dates
overlap, or a re-run of the same ticker, cost at most one network
request per calendar date, ever. Saturdays/Sundays are skipped without
even a cache lookup: Borsa İstanbul's weekly trading calendar (never
open Sat/Sun) is a structural fact of the exchange, not a data value
being guessed. Turkish public holidays have no such shortcut — those are
still requested once and then cached as confirmed-unavailable (HTTP 404;
see :mod:`halka_arz_advisor.market_prices.client`).

A single bad/missing day (a transport error, an unparseable bulletin)
raises immediately rather than silently skipping that date and
returning a gappy series — a caller computing a trading-day-indexed
return must be able to trust that every calendar day in range is either
present in the result or was genuinely a non-trading day, never
"skipped because fetching it failed."
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx

from ..probe.config import ProbeConfig
from .cache import BulletinCache
from .client import BulletinFetchResult, bulletin_url, fetch_bulletin, parse_bulletin_observations
from .config import MarketPricesConfig
from .exceptions import BulletinUnavailableError
from .models import DailyPriceObservation

_SATURDAY = 5
_SUNDAY = 6


def _date_range(start_date: date, end_date: date) -> list[date]:
    if end_date < start_date:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")
    days = (end_date - start_date).days
    return [start_date + timedelta(days=i) for i in range(days + 1)]


def _ensure_cached(
    trading_date: date,
    *,
    cache: BulletinCache,
    config: MarketPricesConfig,
    probe_config: ProbeConfig,
    client: httpx.Client | None,
) -> None:
    """Populate ``cache`` for ``trading_date`` if it isn't already
    resolved one way or the other. Never re-fetches an already-cached
    (positive or negative) date."""
    if cache.get(trading_date) is not None or cache.is_confirmed_unavailable(trading_date):
        return
    try:
        result: BulletinFetchResult = fetch_bulletin(trading_date, config=config, probe_config=probe_config, client=client)
    except BulletinUnavailableError:
        cache.put_unavailable(trading_date, source_url=bulletin_url(trading_date, config=config))
        return
    observations = parse_bulletin_observations(result)
    cache.put_available(result, observations)


def get_observations(
    ticker: str,
    start_date: date,
    end_date: date,
    *,
    cache: BulletinCache,
    config: MarketPricesConfig | None = None,
    probe_config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
) -> tuple[DailyPriceObservation, ...]:
    """Every cached-or-fetched trading-day observation for ``ticker``
    within ``[start_date, end_date]`` inclusive, sorted ascending by
    ``trading_date``. A calendar date with no bulletin (weekend,
    holiday) or a bulletin that simply doesn't list ``ticker`` (not yet
    listed, delisted, suspended that day) contributes nothing — this is
    never padded or interpolated.

    Raises whatever :func:`~halka_arz_advisor.market_prices.client.fetch_bulletin`
    raises for a genuine fetch/parse failure (anything other than the
    documented 404 "no bulletin" case) — a caller building a
    trading-day-indexed return series must not silently treat a fetch
    failure as "not a trading day."
    """
    cfg = config or MarketPricesConfig()
    pcfg = probe_config or ProbeConfig()

    results: list[DailyPriceObservation] = []
    for day in _date_range(start_date, end_date):
        if day.weekday() in (_SATURDAY, _SUNDAY):
            continue
        _ensure_cached(day, cache=cache, config=cfg, probe_config=pcfg, client=client)
        cached = cache.get(day)
        if cached is None:
            continue
        match = next((o for o in cached.observations if o.ticker == ticker), None)
        if match is not None:
            results.append(match)

    return tuple(sorted(results, key=lambda o: o.trading_date))
