"""Custom exceptions for the SPK API client.

Kept as a separate module (rather than living in client.py or models.py)
so both can import them without a circular dependency.
"""

from __future__ import annotations


class SpkApiError(Exception):
    """Base class for all SPK API client errors."""


class SpkTransportError(SpkApiError):
    """Network-level failure (timeout, connection error) after exhausting retries."""


class SpkResponseError(SpkApiError):
    """The HTTP response itself was invalid: bad status, non-JSON content-type,
    or a body that doesn't parse as JSON."""


class SpkSchemaError(SpkApiError):
    """The parsed JSON did not match the documented ``IlkHalkaArzVerileriBilgi``
    schema — wrong top-level shape, wrong field type, or an unexpected field."""


class SpkDiscoveryError(SpkApiError):
    """OpenAPI-driven discovery failed: no (or more than one) matching
    operation was found, or a referenced component schema could not be
    resolved. Raised instead of guessing which endpoint/schema to use."""


class SpkApplicationTableError(SpkApiError):
    """The SPK IPO application page's HTML didn't contain a table this
    parser can confidently identify as the application list — no table
    at all, or none matching the expected header/shape. Per-row problems
    (a bad date, an empty company name) are *not* raised as this error;
    they're collected as invalid rows instead, see
    :mod:`halka_arz_advisor.spk.application_list`."""
