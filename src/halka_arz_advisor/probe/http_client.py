"""A small, polite HTTP client wrapper with bounded retries.

Only transient failures are retried: HTTP 429/5xx responses and
network-level transport errors (connect/read timeouts, connection
resets, DNS hiccups). Anything else — including 4xx client errors other
than 429 — is returned or raised immediately so callers can record it.
"""

from __future__ import annotations

import logging
import time

import httpx

from .config import ProbeConfig

logger = logging.getLogger(__name__)


def build_client(config: ProbeConfig) -> httpx.Client:
    timeout = httpx.Timeout(
        connect=config.connect_timeout_seconds,
        read=config.read_timeout_seconds,
        write=config.read_timeout_seconds,
        pool=config.connect_timeout_seconds,
    )
    return httpx.Client(
        headers={"User-Agent": config.user_agent},
        timeout=timeout,
        follow_redirects=True,
    )


def fetch_with_retry(
    client: httpx.Client,
    url: str,
    config: ProbeConfig,
    *,
    params: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    """GET ``url``, retrying only transient failures with bounded backoff.

    ``params``/``headers`` are passed straight through to ``client.get``
    for the caller's per-request needs (query args, ``Accept`` overrides)
    on top of whatever the client was built with in ``build_client``.

    Raises the last transport exception if every attempt fails. Returns
    the response (even if it is a non-retryable error status) so the
    caller decides what to record.
    """
    last_exc: Exception | None = None
    attempts = config.max_retries + 1

    for attempt in range(attempts):
        is_last_attempt = attempt == attempts - 1
        try:
            response = client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            last_exc = exc
            if is_last_attempt:
                raise
            _sleep_backoff(config, attempt, reason=f"transport error: {exc}")
            continue

        if response.status_code in config.retry_status_codes and not is_last_attempt:
            _sleep_backoff(
                config, attempt, reason=f"HTTP {response.status_code}"
            )
            continue

        return response

    # Unreachable in practice: the loop above always returns or raises.
    assert last_exc is not None
    raise last_exc


def _sleep_backoff(config: ProbeConfig, attempt: int, *, reason: str) -> None:
    delay = config.backoff_base_seconds * (2**attempt)
    logger.info("retrying after %s (attempt %d), sleeping %.1fs", reason, attempt + 1, delay)
    time.sleep(delay)
