"""Official Borsa İstanbul Pay Piyasası Günlük Bülteni (equity daily
bulletin) market-price provider — fully isolated from
:mod:`halka_arz_advisor.kap`/``spk``/``evds``: no module in this package
imports from any of them, and nothing in those packages imports from
here.

Fetches and caches daily OHLC/volume/traded-value observations per
ticker straight from Borsa İstanbul's own free, public bulletin archive
(see :mod:`halka_arz_advisor.market_prices.client` for the confirmed URL
pattern and provenance) — no Yahoo, Investing.com, paid API, or
undocumented third party. :func:`halka_arz_advisor.market_prices.provider.get_observations`
is the intended entry point for every other caller.
"""

from .cache import BulletinCache, CachedBulletin
from .client import BulletinFetchResult, bulletin_url, fetch_bulletin, parse_bulletin_observations
from .config import MarketPricesConfig
from .exceptions import (
    BulletinParseError,
    BulletinUnavailableError,
    MarketPricesError,
    MarketPricesResponseError,
    MarketPricesTransportError,
)
from .models import DailyPriceObservation
from .parsing import ParsedBulletinRow, parse_bulletin_csv
from .provider import get_observations

__all__ = [
    "BulletinCache",
    "CachedBulletin",
    "BulletinFetchResult",
    "bulletin_url",
    "fetch_bulletin",
    "parse_bulletin_observations",
    "MarketPricesConfig",
    "MarketPricesError",
    "MarketPricesTransportError",
    "MarketPricesResponseError",
    "BulletinUnavailableError",
    "BulletinParseError",
    "DailyPriceObservation",
    "ParsedBulletinRow",
    "parse_bulletin_csv",
    "get_observations",
]
