"""Custom exceptions for the Gemini analysis layer.

Mirrors the shape of :mod:`halka_arz_advisor.kap.exceptions` /
:mod:`halka_arz_advisor.spk.exceptions` for consistency across the
project's external-service clients.
"""

from __future__ import annotations


class GeminiError(Exception):
    """Base class for all Gemini analysis-layer errors."""


class GeminiConfigError(GeminiError):
    """Required configuration (``GEMINI_API_KEY``) is missing or invalid."""


class GeminiUnavailableError(GeminiError):
    """The Gemini API could not be reached, or returned a transient error
    (rate limit / quota exhaustion / server overload) that's expected to
    resolve on a later attempt — callers should treat this as "try again
    later", not as a bug in this integration."""


class GeminiModelNotFoundError(GeminiError):
    """The configured ``GEMINI_MODEL`` is not available to this API key."""


class GeminiResponseError(GeminiError):
    """The Gemini API returned a non-transient error (bad request, auth/
    permission failure) or an unparsable response envelope."""


class GeminiOutputError(GeminiError):
    """The model's own output was not valid JSON, failed schema validation, or
    referenced a disclosure/page that wasn't in the supplied context —
    after the one allowed retry."""
