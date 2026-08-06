"""Orchestrates probing every configured source and persisting results."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .config import ProbeConfig
from .http_client import build_client, fetch_with_retry
from .models import ProbeResult, Source
from .parser import parse_html
from .sources import SOURCES

logger = logging.getLogger(__name__)

RAW_EXTENSION_BY_CONTENT_TYPE = (
    ("html", ".html"),
    ("json", ".json"),
    ("xml", ".xml"),
    ("csv", ".csv"),
    ("pdf", ".pdf"),
)


def _now_iso_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _guess_raw_extension(content_type: str | None) -> str:
    if not content_type:
        return ".bin"
    lowered = content_type.lower()
    for needle, ext in RAW_EXTENSION_BY_CONTENT_TYPE:
        if needle in lowered:
            return ext
    return ".bin"


def _save_raw_response(
    raw_dir: Path, source: Source, run_timestamp: str, response: httpx.Response | None, error: str | None
) -> Path:
    dest = raw_dir / source.name / run_timestamp
    dest.mkdir(parents=True, exist_ok=True)

    meta = {
        "source_name": source.name,
        "requested_url": source.url,
        "run_timestamp_utc": run_timestamp,
        "error": error,
    }
    if response is not None:
        content_type = response.headers.get("content-type")
        ext = _guess_raw_extension(content_type)
        body_path = dest / f"response{ext}"
        body_path.write_bytes(response.content)
        meta.update(
            {
                "final_url": str(response.url),
                "http_status": response.status_code,
                "content_type": content_type,
                "response_size_bytes": len(response.content),
                "headers": dict(response.headers),
                "body_file": body_path.name,
            }
        )
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def probe_source(client: httpx.Client, source: Source, config: ProbeConfig, raw_dir: Path, run_timestamp: str) -> ProbeResult:
    checked_at = _now_iso_utc()
    start = time.monotonic()
    try:
        response = fetch_with_retry(client, source.url, config)
    except httpx.TransportError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        error_message = f"{type(exc).__name__}: {exc}"
        logger.error("probe failed for %s: %s", source.name, error_message)
        _save_raw_response(raw_dir, source, run_timestamp, response=None, error=error_message)
        return ProbeResult(
            source_name=source.name,
            requested_url=source.url,
            checked_at_utc=checked_at,
            elapsed_ms=elapsed_ms,
            error=error_message,
        )

    elapsed_ms = (time.monotonic() - start) * 1000
    _save_raw_response(raw_dir, source, run_timestamp, response=response, error=None)

    content_type = response.headers.get("content-type")
    result = ProbeResult(
        source_name=source.name,
        requested_url=source.url,
        checked_at_utc=checked_at,
        final_url=str(response.url),
        http_status=response.status_code,
        content_type=content_type,
        response_size_bytes=len(response.content),
        elapsed_ms=elapsed_ms,
    )

    if response.status_code >= 400:
        result.error = f"HTTP {response.status_code} {response.reason_phrase}".strip()
        return result

    if content_type and "html" in content_type.lower():
        try:
            parsed = parse_html(response.text, str(response.url))
        except Exception as exc:  # never swallow parsing failures silently
            result.error = f"HTML parsing failed: {type(exc).__name__}: {exc}"
            logger.error("parsing failed for %s: %s", source.name, result.error)
            return result
        result.page_title = parsed.page_title
        result.detected_tables = parsed.detected_tables
        result.detected_links = parsed.detected_links
        result.possible_download_links = parsed.possible_download_links
        result.parsing_notes = parsed.parsing_notes
    else:
        result.parsing_notes.append(
            f"content-type '{content_type}' is not HTML; skipped HTML parsing"
        )

    return result


@dataclass(slots=True)
class ProbeRun:
    run_timestamp: str
    results: list[ProbeResult]


def run_all(
    config: ProbeConfig,
    raw_dir: Path,
    sources: tuple[Source, ...] = SOURCES,
) -> ProbeRun:
    run_timestamp = _run_timestamp()
    results: list[ProbeResult] = []
    with build_client(config) as client:
        for i, source in enumerate(sources):
            if i > 0:
                time.sleep(config.delay_between_requests_seconds)
            results.append(probe_source(client, source, config, raw_dir, run_timestamp))
    return ProbeRun(run_timestamp=run_timestamp, results=results)
