"""Runtime configuration for the Borsa İstanbul daily-bulletin provider.

``DEFAULT_BASE_URL`` and the per-date file naming convention
(``{base_url}/{yyyy}/{mm}/thb{yyyymmdd}1.zip``) were confirmed live on
2026-08-07 by downloading real bulletin ZIPs for several real trading
dates (2024-12-19, 2026-07-17, 2026-07-28, 2026-07-30, 2026-08-05,
2026-08-06) — see :mod:`halka_arz_advisor.market_prices.client` for the
full provenance write-up, including why this free, no-authentication
path was chosen over Borsa İstanbul's paid "BIST DataStore"
(``datastore.borsaistanbul.com``) product, which is a subscription
marketplace with no free access to this same historical daily-bulletin
data (confirmed live: its "Pay Piyasası Verileri" product group reports
"Bu ürün grubu için abonelik mevcut değil" — no subscription available
— with no way to purchase or otherwise access it for this project).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_BASE_URL = "https://www.borsaistanbul.com/data/thb"

# The trailing "1" in the per-date filename is the session number BIST
# encodes in the bulletin filename itself (thb<yyyymmdd><session>.zip);
# every date checked during verification only ever published session
# "1" as a same-day equity closing bulletin, so this is the only value
# this project fetches.
BULLETIN_SESSION = 1


@dataclass(frozen=True, slots=True)
class MarketPricesConfig:
    base_url: str = DEFAULT_BASE_URL
