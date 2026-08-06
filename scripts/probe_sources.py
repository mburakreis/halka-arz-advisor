#!/usr/bin/env python3
"""Phase 0 CLI: probe the official public sources and record what we find.

Usage:
    uv run python scripts/probe_sources.py
    uv run python scripts/probe_sources.py --delay 3 --max-retries 2

This does not scrape or scrape aggressively, does not use a browser, and
does not implement any scoring/recommendation/notification logic — it
only checks reachability and shape of each source and writes a report.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.probe.report import write_json_report, write_markdown_report  # noqa: E402
from halka_arz_advisor.probe.runner import run_all  # noqa: E402


_DEFAULTS = ProbeConfig()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delay", type=float, default=_DEFAULTS.delay_between_requests_seconds,
        help="Seconds to wait between requests to different sources (default: %(default)s)",
    )
    parser.add_argument(
        "--connect-timeout", type=float, default=_DEFAULTS.connect_timeout_seconds,
        help="Connect timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--read-timeout", type=float, default=_DEFAULTS.read_timeout_seconds,
        help="Read timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=_DEFAULTS.max_retries,
        help="Max retries for transient failures (429/5xx/network errors) (default: %(default)s)",
    )
    parser.add_argument(
        "--raw-dir", type=Path, default=PROJECT_ROOT / "data" / "raw",
        help="Directory to store raw responses under (default: %(default)s)",
    )
    parser.add_argument(
        "--report-dir", type=Path, default=PROJECT_ROOT / "data" / "probe-results",
        help="Directory to store JSON/Markdown reports under (default: %(default)s)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def format_status_line(result) -> str:
    if result.ok:
        tag = "OK  "
    else:
        tag = "FAIL"
    status = str(result.http_status) if result.http_status is not None else "---"
    elapsed = f"{result.elapsed_ms:.0f}ms" if result.elapsed_ms is not None else "----"
    size = f"{result.response_size_bytes}B" if result.response_size_bytes is not None else "----"
    extra = f"tables={result.detected_tables} links={result.detected_links} dl={len(result.possible_download_links)}"
    line = f"[{tag}] {result.source_name:<26} {status:>4} {elapsed:>8} {size:>10}  {extra}"
    if result.error:
        line += f"  error={result.error}"
    return line


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = ProbeConfig(
        delay_between_requests_seconds=args.delay,
        connect_timeout_seconds=args.connect_timeout,
        read_timeout_seconds=args.read_timeout,
        max_retries=args.max_retries,
    )

    print(f"Probing sources (delay={config.delay_between_requests_seconds}s, "
          f"timeouts={config.connect_timeout_seconds}/{config.read_timeout_seconds}s, "
          f"max_retries={config.max_retries})...")

    run = run_all(config, raw_dir=args.raw_dir)

    for result in run.results:
        print(format_status_line(result))

    json_path = write_json_report(run.results, args.report_dir, run.run_timestamp)
    md_path = write_markdown_report(run.results, args.report_dir, run.run_timestamp)
    print(f"\nJSON report:     {json_path}")
    print(f"Markdown report: {md_path}")

    failures = [r for r in run.results if not r.ok]
    if failures:
        print(f"\n{len(failures)} of {len(run.results)} source(s) failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
