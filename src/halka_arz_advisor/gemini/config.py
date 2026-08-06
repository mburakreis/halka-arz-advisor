"""Runtime configuration for the Gemini analysis layer.

Read from the environment (see ``.env.example``):

- ``GEMINI_API_KEY`` — **required**, no default. A secret; never logged
  or included in any error message.
- ``GEMINI_MODEL`` — defaults to ``gemini-3.5-flash``. Unlike a locally
  pulled Ollama model, Gemini models are a fixed, hosted catalog, so a
  sensible default is safe here (still overridable).
- ``GEMINI_TIMEOUT_SECONDS`` — defaults to 120.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .exceptions import GeminiConfigError

DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class GeminiConfig:
    api_key: str
    model: str
    timeout_seconds: float


def load_gemini_config_from_env() -> GeminiConfig:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise GeminiConfigError(
            "GEMINI_API_KEY must be set — get one from https://aistudio.google.com/apikey "
            "and set it in .env (see .env.example)."
        )

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL

    timeout_raw = os.environ.get("GEMINI_TIMEOUT_SECONDS", "").strip()
    if timeout_raw:
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise GeminiConfigError(
                f"GEMINI_TIMEOUT_SECONDS must be a number, got {timeout_raw!r}"
            ) from exc
    else:
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS

    return GeminiConfig(api_key=api_key, model=model, timeout_seconds=timeout_seconds)
