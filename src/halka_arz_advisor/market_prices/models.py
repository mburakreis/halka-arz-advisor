"""Data shapes for Borsa İstanbul daily-bulletin market prices.

:class:`DailyPriceObservation` is the cached, immutable unit — see
:mod:`halka_arz_advisor.market_prices.cache`. Mirrors
:class:`halka_arz_advisor.evds.models.EvdsObservation`'s shape
(source provenance kept on every observation, never just on the
fetch/cache layer) for the same reason: once written to disk this is
treated as fixed, and any consumer must still be able to tell exactly
which official document and request it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

SourceSystem = Literal["borsa_istanbul_daily_bulletin"]


@dataclass(frozen=True, slots=True)
class DailyPriceObservation:
    """One ticker's one trading day, as reported in Borsa İstanbul's own
    official Pay Piyasası Günlük Bülteni (equity market daily bulletin).

    ``volume`` is share count (the bulletin's own English header:
    "TOTAL TRADED VOLUME", column ``TOPLAM ISLEM ADEDI``); ``traded_value``
    is the TRY value traded (English header "TOTAL TRADED VALUE", column
    ``TOPLAM ISLEM HACMI``) — deliberately not swapped despite the
    Turkish column names' literal translations ("HACMI" reads as
    "volume", "ADEDI" as "count"), because BIST's own bilingual header
    on the real file disagrees with that literal reading; see
    :mod:`halka_arz_advisor.market_prices.parsing` for the confirmed
    column mapping.
    """

    trading_date: date
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    traded_value: float
    source_url: str
    fetched_at: datetime
    source_system: SourceSystem = "borsa_istanbul_daily_bulletin"
