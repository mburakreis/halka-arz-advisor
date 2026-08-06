"""Custom exceptions for the Ollama analysis layer.

Mirrors the shape of :mod:`halka_arz_advisor.kap.exceptions` /
:mod:`halka_arz_advisor.spk.exceptions` for consistency across the
project's external-service clients.
"""

from __future__ import annotations


class OllamaError(Exception):
    """Base class for all Ollama analysis-layer errors."""


class OllamaConfigError(OllamaError):
    """Required configuration (``OLLAMA_MODEL``) is missing or invalid."""


class OllamaUnavailableError(OllamaError):
    """The Ollama server at ``OLLAMA_BASE_URL`` could not be reached."""


class OllamaModelNotFoundError(OllamaError):
    """The configured ``OLLAMA_MODEL`` is not present on the local Ollama server."""


class OllamaResponseError(OllamaError):
    """The Ollama HTTP API returned a bad status or an unparsable response envelope."""


class OllamaOutputError(OllamaError):
    """The model's own output was not valid JSON, failed schema validation, or
    referenced a disclosure/page that wasn't in the supplied context —
    after the one allowed retry."""
