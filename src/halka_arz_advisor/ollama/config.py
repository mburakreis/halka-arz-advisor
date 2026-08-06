"""Runtime configuration for the local Ollama analysis layer.

Read from the environment (see ``.env.example``):

- ``OLLAMA_BASE_URL`` — defaults to ``http://localhost:11434``.
- ``OLLAMA_MODEL`` — **required**, no default. Deliberately not
  hardcoded: which model is installed locally is entirely up to whoever
  runs this, and guessing one that might not exist would just trade a
  clear config error for a confusing "model not found" one.
- ``OLLAMA_TIMEOUT_SECONDS`` — defaults to 180 (local LLM generation on
  CPU can be slow, unlike the project's other, much shorter, HTTP JSON
  API timeouts).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .exceptions import OllamaConfigError

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    base_url: str
    model: str
    timeout_seconds: float


def load_ollama_config_from_env() -> OllamaConfig:
    model = os.environ.get("OLLAMA_MODEL", "").strip()
    if not model:
        raise OllamaConfigError(
            "OLLAMA_MODEL must be set — no model is hardcoded or assumed. "
            "Set it to the name of a model already pulled locally (see `ollama list`), "
            "e.g. OLLAMA_MODEL=llama3.1:8b. See .env.example."
        )

    base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or DEFAULT_BASE_URL

    timeout_raw = os.environ.get("OLLAMA_TIMEOUT_SECONDS", "").strip()
    if timeout_raw:
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise OllamaConfigError(
                f"OLLAMA_TIMEOUT_SECONDS must be a number, got {timeout_raw!r}"
            ) from exc
    else:
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS

    return OllamaConfig(base_url=base_url.rstrip("/"), model=model, timeout_seconds=timeout_seconds)
