"""Custom exceptions for the Borsa İstanbul daily-bulletin market-price
provider.

Mirrors the shape of :mod:`halka_arz_advisor.evds.exceptions` /
:mod:`halka_arz_advisor.kap.exceptions` for consistency across this
project's external-data providers — but this module is otherwise fully
isolated: nothing here imports from ``evds``, ``kap``, or ``spk``, and
nothing in those packages imports from here.
"""

from __future__ import annotations


class MarketPricesError(Exception):
    """Base class for all market_prices provider errors."""


class MarketPricesTransportError(MarketPricesError):
    """Network-level failure (timeout, connection error) after
    exhausting retries."""


class MarketPricesResponseError(MarketPricesError):
    """The HTTP response was invalid in a way that isn't a documented
    "no bulletin exists" signal: an unexpected non-200/404 status, a
    ZIP that won't unzip, or a ZIP that doesn't contain exactly one CSV
    member."""


class BulletinUnavailableError(MarketPricesError):
    """Confirmed (HTTP 404) that no official bulletin exists for the
    requested trading date — a weekend, a Borsa İstanbul public
    holiday, or a future/unpublished date. Not a transport failure and
    never retried: callers should treat this as an honest "no data",
    not guess or synthesize a value."""


class BulletinParseError(MarketPricesError):
    """The bulletin CSV was fetched successfully but its header didn't
    match the schema confirmed in :mod:`halka_arz_advisor.market_prices.parsing`
    — never silently reinterpreted under a schema assumption that no
    longer holds."""
