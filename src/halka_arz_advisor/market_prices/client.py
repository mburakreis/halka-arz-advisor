"""HTTP client for Borsa İstanbul's official Pay Piyasası Günlük Bülteni
(equity market daily bulletin) archive.

URL pattern and mechanism confirmed live on 2026-08-07, from Borsa
İstanbul's own public "Veriler > Günlük Bülten > Günlük Bülten Arşiv"
page (``https://www.borsaistanbul.com/veriler/gunluk-bulten/gunluk-bulten-arsiv``):
selecting a date for "Pay Piyasası Bültenleri" and clicking its download
icon fires a same-origin request for
``https://www.borsaistanbul.com/data/thb/{yyyy}/{mm}/thb{yyyymmdd}1.zip``
(observed directly via the page's own network traffic) — a static file,
no authentication, no query parameters. Verified against six real dates
(spanning 2024 and 2026, including two confirmed IPO first-trading-days):
every real trading date returns HTTP 200 with an ``application/zip``
body containing exactly one CSV; every checked non-trading date (two
weekend days and a Turkish public holiday, 2026-04-23) returns a clean
HTTP 404 rather than an empty/malformed 200 — treated below as the
provider's own authoritative "no bulletin for this date" signal, never
retried or guessed around.

This is the free, public path — deliberately not
``datastore.borsaistanbul.com`` ("BIST DataStore"), confirmed live to be
a paid subscription marketplace: its own "Pay Piyasası Verileri" product
page reports "Bu ürün grubu için abonelik mevcut değil" (no subscription
available for this product group) with no free tier or purchase flow
exposed, and per this project's own sourcing rules a paid API is out of
scope regardless.

Reuses :mod:`halka_arz_advisor.probe.http_client`'s shared timeout/retry
conventions, like every other client in this project — no
KAP/SPK/EVDS-specific code is imported here.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .config import BULLETIN_SESSION, MarketPricesConfig
from .exceptions import BulletinUnavailableError, MarketPricesResponseError, MarketPricesTransportError
from .models import DailyPriceObservation
from .parsing import parse_bulletin_csv


def bulletin_url(trading_date: date, *, config: MarketPricesConfig) -> str:
    return (
        f"{config.base_url}/{trading_date.year:04d}/{trading_date.month:02d}/"
        f"thb{trading_date.strftime('%Y%m%d')}{BULLETIN_SESSION}.zip"
    )


@dataclass(slots=True)
class BulletinFetchResult:
    """One successfully fetched and parsed bulletin day, with the
    provenance every :class:`~halka_arz_advisor.market_prices.models.DailyPriceObservation`
    it produces carries."""

    trading_date: date
    source_url: str
    fetched_at: datetime
    csv_text: str


def _extract_single_csv_member(zip_bytes: bytes, *, source_url: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise MarketPricesResponseError(
                    f"expected exactly one .csv member in the bulletin ZIP from {source_url}, found {csv_names!r}"
                )
            return archive.read(csv_names[0]).decode("utf-8")
    except zipfile.BadZipFile as exc:
        raise MarketPricesResponseError(f"bulletin from {source_url} was not a valid ZIP file: {exc}") from exc


def fetch_bulletin(
    trading_date: date,
    *,
    config: MarketPricesConfig | None = None,
    probe_config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
) -> BulletinFetchResult:
    """Fetch and unzip one calendar date's official equity bulletin.

    Raises :class:`~halka_arz_advisor.market_prices.exceptions.BulletinUnavailableError`
    on a confirmed HTTP 404 (no bulletin exists for this date — a
    weekend, holiday, or a date with no trading), or
    :class:`~halka_arz_advisor.market_prices.exceptions.MarketPricesTransportError`/
    :class:`~halka_arz_advisor.market_prices.exceptions.MarketPricesResponseError`
    for anything else that isn't a clean, parseable success. Never
    returns a partial or synthesized result.
    """
    cfg = config or MarketPricesConfig()
    pcfg = probe_config or ProbeConfig()
    url = bulletin_url(trading_date, config=cfg)

    owns_client = client is None
    http_client = client or build_client(pcfg)
    try:
        try:
            response = fetch_with_retry(http_client, url, pcfg)
        except httpx.TransportError as exc:
            raise MarketPricesTransportError(f"transport failure fetching bulletin {url}: {exc}") from exc
    finally:
        if owns_client:
            http_client.close()

    if response.status_code == 404:
        raise BulletinUnavailableError(f"no official bulletin published at {url} (HTTP 404)")
    if response.status_code != 200:
        raise MarketPricesResponseError(f"bulletin fetch returned HTTP {response.status_code} for {url}: {response.text[:500]!r}")

    content_type = (response.headers.get("content-type") or "").lower()
    if "zip" not in content_type:
        raise MarketPricesResponseError(f"expected an application/zip bulletin at {url}, got content-type {content_type!r}")

    csv_text = _extract_single_csv_member(response.content, source_url=url)
    return BulletinFetchResult(trading_date=trading_date, source_url=url, fetched_at=datetime.now(UTC), csv_text=csv_text)


def parse_bulletin_observations(result: BulletinFetchResult) -> tuple[DailyPriceObservation, ...]:
    """Turn one fetched bulletin day into every equity ticker's
    :class:`~halka_arz_advisor.market_prices.models.DailyPriceObservation`,
    stamped with this fetch's own provenance."""
    rows = parse_bulletin_csv(result.csv_text)
    return tuple(
        DailyPriceObservation(
            trading_date=row.trading_date,
            ticker=row.ticker,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            traded_value=row.traded_value,
            source_url=result.source_url,
            fetched_at=result.fetched_at,
        )
        for row in rows
    )
