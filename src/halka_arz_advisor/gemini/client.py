"""Minimal wrapper around the official ``google-genai`` SDK for the
Gemini analysis layer.

**Behavior confirmed against a live call with a real API key** (SDK
``google-genai`` 2.17.0):

- ``client.models.list(...)`` / ``client.models.get(model=...)`` — used
  for reachability and model-availability checks. Model names come back
  with a ``models/`` prefix (e.g. ``models/gemini-3.5-flash``); a
  missing model raises :class:`google.genai.errors.ClientError` with
  ``code == 404``.
- ``client.models.generate_content(..., config=GenerateContentConfig(
  response_mime_type="application/json", response_json_schema=<schema>))``
  — structured output via a *raw JSON Schema* (not a Pydantic/genai
  ``Schema`` object); ``response.text`` is the model's JSON string.
  Gemini 3.x models do internal "thinking" before answering, but (unlike
  a local llama.cpp-backed Ollama model sharing one fixed context
  window) this doesn't compete with the final answer for space — the
  full structured JSON still comes back in ``response.text``.
- HTTP errors surface as :class:`google.genai.errors.APIError` (with a
  ``.code`` — 4xx via ``ClientError``, 5xx via ``ServerError``); network-
  level failures (unreachable, timeout) surface as ``httpx`` transport
  exceptions, since the SDK uses ``httpx`` internally.
"""

from __future__ import annotations

from google.genai import Client as _GenaiClient
from google.genai import errors as genai_errors
from google.genai import types as genai_types
import httpx

from .config import GeminiConfig
from .exceptions import GeminiModelNotFoundError, GeminiResponseError, GeminiUnavailableError


def _wrap_api_error(exc: genai_errors.APIError, *, context: str) -> Exception:
    code = exc.code
    if code == 404:
        return GeminiModelNotFoundError(f"Gemini API 404 calling {context}: {exc.message}")
    if code == 429 or (code is not None and 500 <= code < 600):
        return GeminiUnavailableError(
            f"Gemini API temporarily unavailable (HTTP {code}) calling {context} — "
            f"try again later: {exc.message}"
        )
    return GeminiResponseError(f"Gemini API error (HTTP {code}) calling {context}: {exc.message}")


class GeminiClient:
    def __init__(self, config: GeminiConfig, *, client: _GenaiClient | None = None) -> None:
        self._config = config
        self._client = client or _GenaiClient(
            api_key=config.api_key,
            http_options=genai_types.HttpOptions(timeout=int(config.timeout_seconds * 1000)),
        )

    @property
    def model_name(self) -> str:
        return self._config.model

    def __enter__(self) -> "GeminiClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def check_available(self) -> None:
        """Raise :class:`GeminiUnavailableError` if the Gemini API can't
        be reached right now (network failure, rate limit, server error)."""
        try:
            next(iter(self._client.models.list(config=genai_types.ListModelsConfig(page_size=1))), None)
        except httpx.TransportError as exc:
            raise GeminiUnavailableError(f"could not reach the Gemini API: {exc}") from exc
        except genai_errors.APIError as exc:
            raise _wrap_api_error(exc, context="models.list") from exc

    def check_model_available(self) -> None:
        """Raise :class:`GeminiModelNotFoundError` if the configured model
        isn't available to this API key, or :class:`GeminiUnavailableError`
        if the check itself couldn't be completed."""
        try:
            self._client.models.get(model=self._config.model)
        except httpx.TransportError as exc:
            raise GeminiUnavailableError(f"could not reach the Gemini API: {exc}") from exc
        except genai_errors.APIError as exc:
            raise _wrap_api_error(exc, context=f"models.get({self._config.model!r})") from exc

    def generate(self, prompt: str, *, format_schema: dict) -> str:
        """Generate structured JSON output; returns the raw response text
        (a JSON *string* — the caller still needs to ``json.loads`` it)."""
        try:
            response = self._client.models.generate_content(
                model=self._config.model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=format_schema,
                ),
            )
        except httpx.TransportError as exc:
            raise GeminiUnavailableError(f"transport failure calling the Gemini API: {exc}") from exc
        except genai_errors.APIError as exc:
            raise _wrap_api_error(exc, context="models.generate_content") from exc

        text = response.text
        if not isinstance(text, str) or not text:
            raise GeminiResponseError("Gemini generate_content response had no text content")
        return text
