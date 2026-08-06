"""Minimal Telegram Bot API sender — plain HTTP POST, no SDK.

Credentials come from the environment (``TELEGRAM_BOT_TOKEN``,
``TELEGRAM_CHAT_ID`` — see ``.env.example``), never from a config file
or argument, so they don't end up in shell history or logs.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from ..probe.config import ProbeConfig

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramConfigError(Exception):
    """``TELEGRAM_BOT_TOKEN``/``TELEGRAM_CHAT_ID`` are missing or empty."""


class TelegramSendError(Exception):
    """The Telegram API call failed: transport error, bad HTTP status, or ``ok: false``."""


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    bot_token: str
    chat_id: str


def load_credentials_from_env() -> TelegramCredentials:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise TelegramConfigError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set — "
            "copy .env.example to .env and fill in real values, or export them directly"
        )
    return TelegramCredentials(bot_token=token, chat_id=chat_id)


def send_message(
    credentials: TelegramCredentials,
    text: str,
    *,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
) -> None:
    """POST one message via the Bot API's ``sendMessage`` method.

    Retries only 429/5xx (Telegram's own transient-failure codes), using
    the same bounded-backoff policy as the rest of the project
    (:class:`~halka_arz_advisor.probe.config.ProbeConfig`), reimplemented
    here directly rather than reusing
    :func:`halka_arz_advisor.probe.http_client.fetch_with_retry`, which
    is GET-only.
    """
    cfg = config or ProbeConfig()
    url = f"{TELEGRAM_API_BASE}/bot{credentials.bot_token}/sendMessage"
    owns_client = client is None
    http_client = client or httpx.Client(
        timeout=httpx.Timeout(
            connect=cfg.connect_timeout_seconds,
            read=cfg.read_timeout_seconds,
            write=cfg.read_timeout_seconds,
            pool=cfg.connect_timeout_seconds,
        )
    )

    attempts = cfg.max_retries + 1
    response: httpx.Response | None = None
    try:
        for attempt in range(attempts):
            is_last_attempt = attempt == attempts - 1
            try:
                response = http_client.post(url, data={"chat_id": credentials.chat_id, "text": text})
            except httpx.TransportError as exc:
                if is_last_attempt:
                    raise TelegramSendError(f"transport failure sending Telegram message: {exc}") from exc
                time.sleep(cfg.backoff_base_seconds * (2**attempt))
                continue

            if response.status_code in cfg.retry_status_codes and not is_last_attempt:
                time.sleep(cfg.backoff_base_seconds * (2**attempt))
                continue
            break
    finally:
        if owns_client:
            http_client.close()

    assert response is not None
    if response.status_code >= 400:
        raise TelegramSendError(f"Telegram API returned HTTP {response.status_code}: {response.text[:300]!r}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramSendError(f"Telegram API response was not valid JSON: {exc}") from exc

    if not payload.get("ok", False):
        raise TelegramSendError(f"Telegram API responded with ok=false: {payload}")
