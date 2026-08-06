"""Typed clients for SPK's official web services and pages.

Two IPO *data* clients coexist here (both talk to the JSON web service):

- :class:`SpkApiClient` (``client.py``) — the original client, with the
  base URL/path hard-coded after manual verification, and a strict typed
  ``SpkIpoRecord`` model.
- :class:`SpkIpoApiClient` (``ipo_client.py``) — discovers the base
  URL/path/schema from the live OpenAPI document at call time
  (:mod:`halka_arz_advisor.spk.openapi`) and returns raw ``dict``
  records plus a schema validation report instead of a fixed model.

They're kept separate rather than merged so each phase's work stays
independently reviewable.

:class:`SpkApplicationListClient` (``application_list.py``, Phase 1B) is
a different kind of source — an HTML page, not the JSON web service —
listing companies that have *applied* for an IPO (not yet completed).
"""

from .application_list import (
    ApplicationTableParseResult,
    InvalidApplicationRow,
    SpkApplicationListClient,
    SpkIpoApplicationRecord,
)
from .client import SpkApiClient, SpkIpoRawResponse
from .exceptions import (
    SpkApiError,
    SpkApplicationTableError,
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
    "SpkApplicationListClient",
    "SpkIpoApplicationRecord",
    "InvalidApplicationRow",
    "ApplicationTableParseResult",
    "SpkApiError",
    "SpkTransportError",
    "SpkResponseError",
    "SpkSchemaError",
    "SpkDiscoveryError",
    "SpkApplicationTableError",
]
