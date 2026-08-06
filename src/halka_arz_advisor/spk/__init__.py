"""Typed client for SPK's official web services (Phase 1A: IPO data only).

Two IPO clients currently coexist here:

- :class:`SpkApiClient` (``client.py``) — the original client, with the
  base URL/path hard-coded after manual verification, and a strict typed
  ``SpkIpoRecord`` model.
- :class:`SpkIpoApiClient` (``ipo_client.py``) — discovers the base
  URL/path/schema from the live OpenAPI document at call time
  (:mod:`halka_arz_advisor.spk.openapi`) and returns raw ``dict``
  records plus a schema validation report instead of a fixed model.

They're kept separate rather than merged so this phase's
discovery-driven work stays independently reviewable.
"""

from .client import SpkApiClient, SpkIpoRawResponse
from .exceptions import (
    SpkApiError,
    SpkDiscoveryError,
    SpkResponseError,
    SpkSchemaError,
    SpkTransportError,
)
from .ipo_client import SpkIpoApiClient, SpkIpoApiResult
from .models import SpkIpoRecord

__all__ = [
    "SpkApiClient",
    "SpkIpoRawResponse",
    "SpkIpoRecord",
    "SpkIpoApiClient",
    "SpkIpoApiResult",
    "SpkApiError",
    "SpkTransportError",
    "SpkResponseError",
    "SpkSchemaError",
    "SpkDiscoveryError",
]
