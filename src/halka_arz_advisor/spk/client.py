"""Typed client for SPK's documented "İlk Halka Arz Verileri" web service.

Endpoint derivation evidence (see ``docs/spk-ipo-endpoint.md`` for the
full write-up):

1. The OpenAPI document at ``https://ws.spk.gov.tr/swagger/v2/swagger.json``
   declares path ``/BorclanmaAraclari/api/IlkHalkaArzVerileri`` with a GET
   operation taking an integer ``yil`` query parameter and returning an
   array of ``IlkHalkaArzVerileriBilgi``. The document has no ``servers``
   entry; per the OpenAPI 3.0 spec, an absent ``servers`` defaults to the
   root of wherever the document itself was retrieved from — i.e.
   ``https://ws.spk.gov.tr/``.
2. The Swagger UI page at ``https://ws.spk.gov.tr/help/index.html`` loads
   that same spec via the *relative* URL ``/swagger/v2/swagger.json``,
   confirming the docs UI and the API share the ``ws.spk.gov.tr`` host.
3. A documented GET to
   ``https://ws.spk.gov.tr/BorclanmaAraclari/api/IlkHalkaArzVerileri?yil=2024``
   returned HTTP 200 with ``Content-Type: application/json`` and a JSON
   array whose objects match every field in ``IlkHalkaArzVerileriBilgi``.

No alternative or private host was tried or guessed.
"""

from __future__ import annotations

import json

import httpx

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .exceptions import SpkResponseError, SpkSchemaError, SpkTransportError
from .models import SpkIpoRecord, parse_ipo_record

BASE_URL = "https://ws.spk.gov.tr"
IPO_ENDPOINT_PATH = "/BorclanmaAraclari/api/IlkHalkaArzVerileri"


class SpkIpoRawResponse:
    """The raw, pre-normalization API response for one year.

    Kept distinct from the parsed ``list[SpkIpoRecord]`` so a schema
    failure during normalization never loses what the server actually
    sent — callers of :meth:`SpkApiClient.fetch_ipo_raw` always get this
    first, untouched.
    """

    __slots__ = ("year", "requested_url", "http_status", "content_type", "raw_json")

    def __init__(
        self,
        *,
        year: int,
        requested_url: str,
        http_status: int,
        content_type: str | None,
        raw_json: list[dict],
    ) -> None:
        self.year = year
        self.requested_url = requested_url
        self.http_status = http_status
        self.content_type = content_type
        self.raw_json = raw_json


class SpkApiClient:
    """Client for the documented SPK "İlk Halka Arz Verileri" endpoint.

    Reuses the shared probe HTTP infrastructure
    (:mod:`halka_arz_advisor.probe.http_client`) for timeouts and the
    bounded 429/5xx retry policy, so this client behaves consistently
    with the Phase 0 source probes.
    """

    def __init__(self, config: ProbeConfig | None = None, *, client: httpx.Client | None = None) -> None:
        self._config = config or ProbeConfig()
        self._owns_client = client is None
        self._client = client or build_client(self._config)

    def __enter__(self) -> "SpkApiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_ipo_raw(self, year: int) -> SpkIpoRawResponse:
        """Fetch and shape-validate the raw JSON array for ``year``, without
        normalizing individual records yet."""
        url = f"{BASE_URL}{IPO_ENDPOINT_PATH}"

        try:
            response = fetch_with_retry(
                self._client,
                url,
                self._config,
                params={"yil": year},
                headers={"Accept": "application/json"},
            )
        except httpx.TransportError as exc:
            raise SpkTransportError(
                f"transport failure fetching SPK IPO data for year {year} from {url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise SpkResponseError(
                f"SPK IPO endpoint returned HTTP {response.status_code} for year {year}: "
                f"{response.text[:500]!r}"
            )

        content_type = response.headers.get("content-type")
        if not content_type or "json" not in content_type.lower():
            raise SpkResponseError(
                f"SPK IPO endpoint returned non-JSON content-type {content_type!r} for year {year}"
            )

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SpkResponseError(
                f"SPK IPO endpoint returned a body that is not valid JSON for year {year}: {exc}"
            ) from exc

        if not isinstance(payload, list):
            raise SpkSchemaError(
                f"expected a top-level JSON array from the SPK IPO endpoint for year {year}, "
                f"got {type(payload).__name__}"
            )
        for i, item in enumerate(payload):
            if not isinstance(item, dict):
                raise SpkSchemaError(
                    f"expected a JSON object at index {i} of the SPK IPO array for year {year}, "
                    f"got {type(item).__name__}"
                )

        return SpkIpoRawResponse(
            year=year,
            requested_url=str(response.url),
            http_status=response.status_code,
            content_type=content_type,
            raw_json=payload,
        )

    def get_initial_public_offerings(self, year: int) -> list[SpkIpoRecord]:
        """Fetch and normalize IPO records for ``year``.

        Raises :class:`~halka_arz_advisor.spk.exceptions.SpkTransportError`,
        :class:`~halka_arz_advisor.spk.exceptions.SpkResponseError`, or
        :class:`~halka_arz_advisor.spk.exceptions.SpkSchemaError` — never
        returns a partially-invented or zero-filled record.
        """
        raw = self.fetch_ipo_raw(year)
        return [parse_ipo_record(item, index=i) for i, item in enumerate(raw.raw_json)]
