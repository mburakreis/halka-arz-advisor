"""Custom exceptions for the EVDS (TCMB Elektronik Veri Dağıtım Sistemi)
market-context data provider.

Mirrors the shape of :mod:`halka_arz_advisor.kap.exceptions` /
:mod:`halka_arz_advisor.spk.exceptions` for consistency across this
project's external-service clients — but this module is otherwise fully
isolated from both: :mod:`halka_arz_advisor.evds` never imports from
:mod:`halka_arz_advisor.kap`, and nothing in ``kap`` imports from here.
"""

from __future__ import annotations


class EvdsError(Exception):
    """Base class for all EVDS provider errors."""


class EvdsConfigError(EvdsError):
    """``EVDS_API_KEY`` is missing or empty. Callers should treat this as
    "the provider is unavailable" and degrade gracefully (skip the
    refresh, keep serving whatever is already cached) — never as a
    reason to fail a larger pipeline run."""


class EvdsTransportError(EvdsError):
    """Network-level failure (timeout, connection error) after
    exhausting retries."""


class EvdsResponseError(EvdsError):
    """The HTTP response itself was invalid: a non-2xx status (including
    an invalid/expired API key), non-JSON content-type, or a body that
    doesn't parse as JSON."""
