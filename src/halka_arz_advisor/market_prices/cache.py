"""Disk cache of official Borsa İstanbul daily bulletins — one file per
**calendar date**, not per ticker, so requesting price history for
several IPOs that share a trading date (a common case: several
companies list in the same week, and every already-listed ticker
overlaps most of its own return-window dates with every other) never
re-downloads or re-requests the same day's bulletin twice.

Caches the full parsed-and-fetched bulletin day (every equity ticker,
not just whichever ticker triggered the fetch) — a
:class:`CachedBulletin` — and, separately, a negative-cache marker for a
calendar date BIST has confirmed (HTTP 404) has no bulletin at all (a
weekend, a public holiday), so a caller sweeping a trading-date range
doesn't re-request every non-trading day on every run.

Bulletin days are immutable once published (Borsa İstanbul does not
retroactively revise a closed trading day's own bulletin), so — mirroring
:class:`halka_arz_advisor.evds.cache.EvdsCache`'s "never overwrite an
already-cached date" contract — this cache never re-fetches a date it
already has an entry for, positive or negative.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .client import BulletinFetchResult
from .models import DailyPriceObservation


@dataclass(frozen=True, slots=True)
class CachedBulletin:
    trading_date: date
    source_url: str
    fetched_at: datetime
    observations: tuple[DailyPriceObservation, ...]


def _observation_to_dict(obs: DailyPriceObservation) -> dict:
    return {
        "ticker": obs.ticker,
        "open": obs.open,
        "high": obs.high,
        "low": obs.low,
        "close": obs.close,
        "volume": obs.volume,
        "traded_value": obs.traded_value,
    }


def _observation_from_dict(data: dict, *, trading_date: date, source_url: str, fetched_at: datetime) -> DailyPriceObservation:
    return DailyPriceObservation(
        trading_date=trading_date,
        ticker=data["ticker"],
        open=data["open"],
        high=data["high"],
        low=data["low"],
        close=data["close"],
        volume=data["volume"],
        traded_value=data["traded_value"],
        source_url=source_url,
        fetched_at=fetched_at,
    )


class BulletinCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, trading_date: date) -> Path:
        return self.directory / f"{trading_date.isoformat()}.json"

    def get(self, trading_date: date) -> CachedBulletin | None:
        """Returns ``None`` for a date that has *never* been cached
        (available or confirmed-unavailable) — see :meth:`is_confirmed_unavailable`
        to distinguish "never checked" from "checked, no bulletin"."""
        path = self._path(trading_date)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["status"] != "available":
            return None
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        return CachedBulletin(
            trading_date=trading_date,
            source_url=data["source_url"],
            fetched_at=fetched_at,
            observations=tuple(
                _observation_from_dict(row, trading_date=trading_date, source_url=data["source_url"], fetched_at=fetched_at)
                for row in data["rows"]
            ),
        )

    def is_confirmed_unavailable(self, trading_date: date) -> bool:
        path = self._path(trading_date)
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        return bool(data["status"] == "unavailable")

    def put_available(self, result: BulletinFetchResult, observations: tuple[DailyPriceObservation, ...]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "available",
            "trading_date": result.trading_date.isoformat(),
            "source_url": result.source_url,
            "fetched_at": result.fetched_at.isoformat(),
            "rows": [_observation_to_dict(o) for o in observations],
        }
        self._path(result.trading_date).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def put_unavailable(self, trading_date: date, *, source_url: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "unavailable",
            "trading_date": trading_date.isoformat(),
            "source_url": source_url,
            "checked_at": datetime.now(UTC).isoformat(),
        }
        self._path(trading_date).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
