"""Minimal client for the local Ollama HTTP API — no SDK, no third-party
Ollama package.

**Endpoints used** (confirmed against Ollama's official API reference,
https://github.com/ollama/ollama/blob/main/docs/api.md):

- ``GET {base_url}/api/version`` — reachability check.
- ``GET {base_url}/api/tags`` — lists locally available models
  (``{"models": [{"name": "llama3.1:8b", ...}, ...]}``), used to verify
  the configured model is actually pulled before analysis starts.
- ``POST {base_url}/api/generate`` — structured-output generation via
  Ollama's documented ``format: <json schema>`` parameter with
  ``stream: false``; the model's JSON output comes back as a *string* in
  the response envelope's ``response`` field (still needs its own
  ``json.loads``), not as a nested JSON object.
"""

from __future__ import annotations

import json

import httpx

from .config import OllamaConfig
from .exceptions import OllamaModelNotFoundError, OllamaResponseError, OllamaUnavailableError


class OllamaClient:
    def __init__(self, config: OllamaConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=config.base_url,
            timeout=httpx.Timeout(
                connect=10.0, read=config.timeout_seconds, write=config.timeout_seconds, pool=10.0
            ),
        )

    @property
    def model_name(self) -> str:
        return self._config.model

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get_json(self, path: str) -> dict:
        try:
            response = self._client.get(path)
        except httpx.TransportError as exc:
            raise OllamaUnavailableError(
                f"could not reach Ollama server at {self._config.base_url}: {exc}"
            ) from exc
        if response.status_code >= 400:
            raise OllamaUnavailableError(
                f"Ollama server at {self._config.base_url} returned HTTP {response.status_code} for {path}"
            )
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OllamaResponseError(f"Ollama {path} response was not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise OllamaResponseError(f"Ollama {path} response was not a JSON object")
        return data

    def check_available(self) -> str:
        """Return the server's reported version, or raise :class:`OllamaUnavailableError`."""
        data = self._get_json("/api/version")
        version = data.get("version")
        return version if isinstance(version, str) else ""

    def list_models(self) -> list[str]:
        data = self._get_json("/api/tags")
        models = data.get("models")
        if not isinstance(models, list):
            raise OllamaResponseError("Ollama /api/tags response is missing a 'models' array")
        return [item["name"] for item in models if isinstance(item, dict) and isinstance(item.get("name"), str)]

    def check_model_available(self) -> None:
        """Raise :class:`OllamaModelNotFoundError` if the configured model isn't pulled locally.

        Model names carry an optional ``:tag`` suffix defaulting to
        ``:latest`` (Ollama's own convention) — matches with or without
        an explicit tag on either side.
        """
        available = self.list_models()
        wanted = self._config.model
        available_untagged = {name.split(":", 1)[0] for name in available}
        if wanted in available or f"{wanted}:latest" in available or wanted in available_untagged:
            return
        raise OllamaModelNotFoundError(
            f"model {wanted!r} is not available on the local Ollama server at {self._config.base_url} "
            f"(available: {sorted(available)}). Pull it first: `ollama pull {wanted}`."
        )

    def generate(self, prompt: str, *, format_schema: dict) -> str:
        """``POST /api/generate`` with structured output; returns the raw
        response text (a JSON *string*, per the model's output — the
        caller still needs to ``json.loads`` it)."""
        payload = {"model": self._config.model, "prompt": prompt, "stream": False, "format": format_schema}
        try:
            response = self._client.post("/api/generate", json=payload)
        except httpx.TransportError as exc:
            raise OllamaUnavailableError(f"transport failure calling Ollama /api/generate: {exc}") from exc
        if response.status_code >= 400:
            raise OllamaResponseError(
                f"Ollama /api/generate returned HTTP {response.status_code}: {response.text[:500]!r}"
            )
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise OllamaResponseError(f"Ollama /api/generate response was not valid JSON: {exc}") from exc
        text = data.get("response") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise OllamaResponseError("Ollama /api/generate response is missing a string 'response' field")
        return text
