"""Refresh the local EVDS cache — the only place in this package that
decides *when* to make a network call: batches every same-frequency
series into one request and only ever asks for the gap since what's
already cached (see :mod:`halka_arz_advisor.evds.cache`), per EVDS's
own guidance to batch compatible series and avoid frequent polling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .cache import EvdsCache
from .client import EvdsClient
from .config import EvdsConfig
from .exceptions import EvdsError
from .registry import daily_series_keys, get_series_spec, monthly_series_keys

# How far back to seed a series that has never been cached before —
# wide enough for the 120-trading-day BIST return window (plus a
# holiday/gap margin) and 13 months of CPI history for a year-over-year
# comparison, without ever scanning unlimited history.
_INITIAL_LOOKBACK_DAYS = {"daily": 260, "monthly": 400}


@dataclass(slots=True)
class RefreshOutcome:
    refreshed_series_keys: list[str] = field(default_factory=list)
    skipped_no_key: bool = False
    failed_series_keys: dict[str, str] = field(default_factory=dict)


def _lookback_start(cache: EvdsCache, series_key: str, frequency: str, reference_date: date) -> date:
    latest = cache.latest_observation_date(series_key)
    if latest is not None:
        return latest + timedelta(days=1)
    return reference_date - timedelta(days=_INITIAL_LOOKBACK_DAYS[frequency])


def refresh_market_context(
    cache: EvdsCache,
    *,
    config: EvdsConfig | None,
    client: EvdsClient | None = None,
    reference_date: date | None = None,
) -> RefreshOutcome:
    """Fetch whatever's missing since each series' own last-cached date
    (or a bounded initial lookback) and merge it into ``cache``.

    ``config is None`` (``EVDS_API_KEY`` unset) is not an error —
    returns immediately with ``skipped_no_key=True``, leaving the
    existing cache exactly as it was, so a caller (e.g. GitHub Actions
    without the secret configured) never fails just because EVDS isn't
    reachable this run. A per-batch network failure is caught and
    recorded in ``failed_series_keys`` rather than raised, so daily and
    monthly series never block each other.
    """
    outcome = RefreshOutcome()
    if config is None:
        outcome.skipped_no_key = True
        return outcome

    ref = reference_date or date.today()
    owns_client = client is None
    evds_client = client or EvdsClient(config)

    try:
        for keys, frequency in ((daily_series_keys(), "daily"), (monthly_series_keys(), "monthly")):
            if not keys:
                continue
            window_start = min(_lookback_start(cache, key, frequency, ref) for key in keys)
            if window_start > ref:
                continue
            specs = [get_series_spec(key) for key in keys]
            try:
                by_key = evds_client.fetch_observations(specs, window_start, ref)
            except EvdsError as exc:
                for key in keys:
                    outcome.failed_series_keys[key] = str(exc)
                continue
            for key, observations in by_key.items():
                cache.merge_and_save(key, observations)
                outcome.refreshed_series_keys.append(key)
    finally:
        if owns_client:
            evds_client.close()

    return outcome
