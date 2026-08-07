"""HTTP client for TCMB's EVDS REST service.

Endpoint and authentication were not guessed — confirmed live on
2026-08-07 against an authenticated account (see
:mod:`halka_arz_advisor.evds.registry`'s module docstring for how each
pinned series code was found and verified the same way):

- Base URL ``https://evds3.tcmb.gov.tr/igmevdsms-dis`` — the REST
  service kept working at this path through TCMB's front-end migration
  from ``evds2.tcmb.gov.tr`` to ``evds3.tcmb.gov.tr`` (only the
  browsable website moved; ``evds2``'s own ``/service/evds/`` path now
  just redirects to the ``evds3`` website).
- Authentication is an HTTP header, ``key: <EVDS_API_KEY>`` — *not* a
  ``?key=`` query parameter, which is what EVDS used before an
  April 2024 change and is a common stale example still found in older
  write-ups.
- The data endpoint's own query parameters (``series``, ``startDate``,
  ``endDate``, ``type``) are appended directly after the base URL with
  ``&``, deliberately *without* a leading ``?`` — confirmed live:
  including a ``?`` here causes EVDS to redirect to the website instead
  of returning JSON, matching several independently-written EVDS client
  libraries' own (unexplained, but consistently applied) convention.

Reuses this project's shared HTTP conventions
(:mod:`halka_arz_advisor.probe.http_client`) for timeouts and the
bounded 429/5xx retry policy — no KAP-specific code is imported
anywhere in this package.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime

import httpx

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .config import EvdsConfig
from .exceptions import EvdsResponseError, EvdsTransportError
from .models import EvdsObservation
from .parsing import parse_evds_items
from .registry import EvdsSeriesSpec

_DATE_FORMAT = "%d-%m-%Y"


def fetch_series_raw(
    series_specs: Sequence[EvdsSeriesSpec],
    start_date: date,
    end_date: date,
    *,
    config: EvdsConfig,
    probe_config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
) -> list[dict]:
    """One EVDS request for one or more series — batch every
    same-frequency series this project needs into a single call rather
    than fetching each separately (see
    :func:`halka_arz_advisor.evds.registry.daily_series_keys`), per
    EVDS's own guidance to avoid unnecessary/frequent polling. Returns
    the raw ``items`` array, unparsed — see
    :func:`halka_arz_advisor.evds.parsing.parse_evds_items`.
    """
    if not series_specs:
        return []

    cfg = probe_config or ProbeConfig()
    owns_client = client is None
    http_client = client or build_client(cfg)

    series_param = "-".join(spec.series_code for spec in series_specs)
    url = (
        f"{config.base_url}/series={series_param}"
        f"&startDate={start_date.strftime(_DATE_FORMAT)}"
        f"&endDate={end_date.strftime(_DATE_FORMAT)}"
        "&type=json"
    )

    try:
        try:
            response = fetch_with_retry(http_client, url, cfg, headers={"key": config.api_key})
        except httpx.TransportError as exc:
            raise EvdsTransportError(f"transport failure fetching EVDS series {series_param}: {exc}") from exc
    finally:
        if owns_client:
            http_client.close()

    if response.status_code >= 400:
        raise EvdsResponseError(
            f"EVDS returned HTTP {response.status_code} for series {series_param}: {response.text[:500]!r}"
        )

    content_type = response.headers.get("content-type") or ""
    if "json" not in content_type.lower():
        raise EvdsResponseError(f"EVDS returned non-JSON content-type {content_type!r} for series {series_param}")

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise EvdsResponseError(
            f"EVDS returned a body that is not valid JSON for series {series_param}: {exc}"
        ) from exc

    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise EvdsResponseError(
            f"expected an object with an 'items' array from EVDS for series {series_param}, got {data!r}"
        )
    return data["items"]


class EvdsClient:
    """Client for EVDS's data endpoint, returning normalized observations."""

    def __init__(self, config: EvdsConfig, *, probe_config: ProbeConfig | None = None, client: httpx.Client | None = None) -> None:
        self._config = config
        self._probe_config = probe_config or ProbeConfig()
        self._owns_client = client is None
        self._client = client or build_client(self._probe_config)

    def __enter__(self) -> "EvdsClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_observations(
        self, series_specs: Sequence[EvdsSeriesSpec], start_date: date, end_date: date
    ) -> dict[str, list[EvdsObservation]]:
        """Fetch and normalize observations for ``series_specs`` over
        ``[start_date, end_date]`` — a single batched request when more
        than one spec is given.

        Raises :class:`~halka_arz_advisor.evds.exceptions.EvdsTransportError`
        or :class:`~halka_arz_advisor.evds.exceptions.EvdsResponseError`
        on failure — callers (see
        :mod:`halka_arz_advisor.evds.refresh`) are expected to catch
        these and degrade gracefully rather than fail a larger pipeline
        run.
        """
        raw_items = fetch_series_raw(
            series_specs, start_date, end_date, config=self._config, probe_config=self._probe_config, client=self._client
        )
        return parse_evds_items(raw_items, series_specs, fetched_at=datetime.now(UTC))
