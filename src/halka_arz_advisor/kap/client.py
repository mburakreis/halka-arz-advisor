"""Client for KAP's (Kamuyu Aydınlatma Platformu / Public Disclosure
Platform) real disclosure-list web API.

**Endpoint and request payload were not guessed.** Per this phase's
brief, they were determined by inspecting open-source projects that
had already reverse-engineered this API, then confirmed live:

- `enciyo/kap-tr-sdk <https://github.com/enciyo/kap-tr-sdk>`_
  (``kap_sdk/kap_client.py``) established the endpoint URL
  (``POST https://www.kap.org.tr/tr/api/disclosure/list/main``) and the
  ``{"disclosureBasic": {...}, "disclosureDetail": {...}}`` response
  shape (cross-checked against its committed sample,
  ``samples/announcements.json``).
- Its exact payload — ``fundTypes``/``memberTypes``/singular
  ``disclosureType`` — returned **HTTP 500** when tried live (2026-08-06).
  A captured real browser request in
  `develooper1994/kap-cli <https://github.com/develooper1994/kap-cli>`_
  (``Anasayfa/main-service/main-sirket-bildirimleri.md``) showed the
  field is actually the **plural** ``disclosureTypes`` and that
  ``fundTypes`` isn't sent at all — switching to that shape fixed the
  request (confirmed HTTP 200, 200+ disclosures on a single day, ~6500
  over a 30-day range).
- `cemsinano/pykap <https://github.com/cemsinano/pykap>`_
  (``pykap/bist/BISTCompany.py``) confirmed the public notification page
  URL pattern, ``https://www.kap.org.tr/tr/Bildirim/{disclosureIndex}``.

No third-party KAP package is installed or imported — only the shared
project HTTP conventions (:mod:`halka_arz_advisor.probe.http_client`).
``fetch_with_retry`` there is GET-only, so — following the same
precedent as :func:`halka_arz_advisor.notify.telegram.send_message` —
the bounded 429/5xx retry loop is reimplemented directly here for this
POST endpoint.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

import httpx

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client
from .exceptions import KapResponseError, KapSchemaError, KapTransportError
from .models import KapDisclosure, parse_disclosure

KAP_DISCLOSURE_LIST_URL = "https://www.kap.org.tr/tr/api/disclosure/list/main"

# Broad, live-tested defaults (see module docstring for provenance).
# Disclosure classes: ODA=material event, FR=financial report,
# DUY=regulatory-authority announcement, DG=other, CA=corporate action.
# Member types: IGS=BIST-traded companies, DDK=regulators,
# YK=investment firms (where IPO intermediaries file Fiyat Tespit
# Raporu/Halka Arz Sonuçları under their own membership), PYS=portfolio
# management companies, KVH/DG=other member categories.
DEFAULT_DISCLOSURE_TYPES: tuple[str, ...] = ("ODA", "FR", "DUY", "DG", "CA")
DEFAULT_MEMBER_TYPES: tuple[str, ...] = ("IGS", "DDK", "YK", "PYS", "KVH", "DG")


def fetch_disclosures_raw(
    from_date: date,
    to_date: date,
    *,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    disclosure_types: tuple[str, ...] = DEFAULT_DISCLOSURE_TYPES,
    member_types: tuple[str, ...] = DEFAULT_MEMBER_TYPES,
) -> list[dict]:
    """POST to KAP's disclosure list endpoint; return the raw JSON array,
    shape-checked but not yet normalized into :class:`KapDisclosure`."""
    cfg = config or ProbeConfig()
    owns_client = client is None
    http_client = client or build_client(cfg)

    payload = {
        "fromDate": from_date.strftime("%d.%m.%Y"),
        "toDate": to_date.strftime("%d.%m.%Y"),
        "disclosureTypes": list(disclosure_types),
        "memberTypes": list(member_types),
    }

    attempts = cfg.max_retries + 1
    response: httpx.Response | None = None
    try:
        for attempt in range(attempts):
            is_last_attempt = attempt == attempts - 1
            try:
                response = http_client.post(
                    KAP_DISCLOSURE_LIST_URL, json=payload, headers={"Accept": "application/json"}
                )
            except httpx.TransportError as exc:
                if is_last_attempt:
                    raise KapTransportError(
                        f"transport failure fetching KAP disclosures from {KAP_DISCLOSURE_LIST_URL}: {exc}"
                    ) from exc
                time.sleep(cfg.backoff_base_seconds * (2**attempt))
                continue

            if response.status_code in cfg.retry_status_codes and not is_last_attempt:
                time.sleep(cfg.backoff_base_seconds * (2**attempt))
                continue
            break
    finally:
        if owns_client:
            http_client.close()

    assert response is not None
    if response.status_code >= 400:
        raise KapResponseError(
            f"KAP disclosure list endpoint returned HTTP {response.status_code}: {response.text[:500]!r}"
        )

    content_type = response.headers.get("content-type") or ""
    if "json" not in content_type.lower():
        raise KapResponseError(
            f"KAP disclosure list endpoint returned non-JSON content-type {content_type!r}"
        )

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise KapResponseError(
            f"KAP disclosure list endpoint returned a body that is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, list):
        raise KapSchemaError(
            f"expected a top-level JSON array from the KAP disclosure list endpoint, got {type(data).__name__}"
        )

    return data


class KapClient:
    """Client for KAP's disclosure-list endpoint, returning normalized records.

    Uses the same shared HTTP conventions (timeouts, retries,
    User-Agent) as the rest of the project via
    :mod:`halka_arz_advisor.probe.http_client`.
    """

    def __init__(self, config: ProbeConfig | None = None, *, client: httpx.Client | None = None) -> None:
        self._config = config or ProbeConfig()
        self._owns_client = client is None
        self._client = client or build_client(self._config)

    def __enter__(self) -> "KapClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_disclosures(self, from_date: date, to_date: date) -> list[KapDisclosure]:
        """Fetch and normalize every disclosure published in ``[from_date, to_date]``.

        Raises :class:`~halka_arz_advisor.kap.exceptions.KapTransportError`,
        :class:`~halka_arz_advisor.kap.exceptions.KapResponseError`, or
        :class:`~halka_arz_advisor.kap.exceptions.KapSchemaError` — a
        malformed item fails the whole fetch rather than being silently
        dropped, matching how :mod:`halka_arz_advisor.spk.client` treats
        its own well-structured JSON API.
        """
        raw_items = fetch_disclosures_raw(from_date, to_date, config=self._config, client=self._client)
        return [parse_disclosure(item) for item in raw_items]

    def fetch_recent_disclosures(self, days: int = 30) -> list[KapDisclosure]:
        """Convenience wrapper: fetch the last ``days`` days up to today."""
        today = date.today()
        return self.fetch_disclosures(today - timedelta(days=days), today)
