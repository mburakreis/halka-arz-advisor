"""Custom exceptions for the KAP disclosure client.

Mirrors the shape of :mod:`halka_arz_advisor.spk.exceptions` for
consistency across the project's HTTP clients.
"""

from __future__ import annotations


class KapApiError(Exception):
    """Base class for all KAP API client errors."""


class KapTransportError(KapApiError):
    """Network-level failure (timeout, connection error) after exhausting retries."""


class KapResponseError(KapApiError):
    """The HTTP response itself was invalid: bad status, non-JSON content-type,
    or a body that doesn't parse as JSON."""


class KapSchemaError(KapApiError):
    """The parsed JSON did not match the expected disclosure-list shape —
    wrong top-level type, or an individual disclosure item missing a
    required field."""
