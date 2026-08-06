#!/usr/bin/env python3
"""Phase 1B CLI: fetch and parse SPK's IPO application list into typed records.

Source: https://spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu

Usage:
    uv run python scripts/fetch_spk_ipo_applications.py

This is a list of companies that have *applied* for an IPO — it is not
matched against completed IPOs (a later phase), and this script does not
score, recommend, notify, or store anything beyond the raw/normalized
files it writes to disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import (  # noqa: E402
    ApplicationTableParseResult,
    SpkApplicationListClient,
    SpkIpoApplicationRecord,
    parse_application_table,
)
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402

_DEFAULTS = ProbeConfig()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connect-timeout", type=float, default=_DEFAULTS.connect_timeout_seconds)
    parser.add_argument("--read-timeout", type=float, default=_DEFAULTS.read_timeout_seconds)
    parser.add_argument("--max-retries", type=int, default=_DEFAULTS.max_retries)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data" / "raw")
    parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "data" / "probe-results")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def _run_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def find_duplicate_company_names(records: tuple[SpkIpoApplicationRecord, ...]) -> dict[str, list[str]]:
    """Company names appearing on more than one row, with each row's date.

    Exact-string match on the already-trimmed, un-normalized company name
    — this deliberately does not fuzzy-match near-duplicates (different
    punctuation/casing) or decide which row is the "real" one.
    """
    counts = Counter(r.company_name for r in records)
    duplicates: dict[str, list[str]] = {}
    for name, count in counts.items():
        if count > 1:
            duplicates[name] = sorted(r.application_date.isoformat() for r in records if r.company_name == name)
    return duplicates


def build_report(result: ApplicationTableParseResult) -> dict:
    records = result.records
    earliest = min(records, key=lambda r: r.application_date) if records else None
    latest = max(records, key=lambda r: r.application_date) if records else None
    duplicates = find_duplicate_company_names(records)

    return {
        "total_rows_scanned": len(records) + len(result.invalid_rows),
        "valid_record_count": len(records),
        "invalid_row_count": len(result.invalid_rows),
        "earliest_application": (
            {"company_name": earliest.company_name, "application_date": earliest.application_date.isoformat()}
            if earliest
            else None
        ),
        "latest_application": (
            {"company_name": latest.company_name, "application_date": latest.application_date.isoformat()}
            if latest
            else None
        ),
        "duplicate_company_names": duplicates,
        "duplicate_company_name_count": len(duplicates),
        "invalid_rows": [
            {"row_index": row.row_index, "reason": row.reason, "raw_row": list(row.raw_row)}
            for row in result.invalid_rows
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_timestamp = _run_timestamp()

    config = ProbeConfig(
        connect_timeout_seconds=args.connect_timeout,
        read_timeout_seconds=args.read_timeout,
        max_retries=args.max_retries,
    )

    print("Fetching SPK IPO application list...")
    with SpkApplicationListClient(config) as client:
        try:
            raw = client.fetch_raw()
        except SpkApiError as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        raw_dest = args.raw_dir / "spk_ipo_application_list" / run_timestamp
        raw_dest.mkdir(parents=True, exist_ok=True)
        (raw_dest / "response.html").write_text(raw.html, encoding="utf-8")
        (raw_dest / "meta.json").write_text(
            json.dumps(
                {
                    "requested_url": raw.requested_url,
                    "final_url": raw.final_url,
                    "http_status": raw.http_status,
                    "content_type": raw.content_type,
                    "elapsed_ms": raw.elapsed_ms,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        try:
            result = parse_application_table(raw.html)
        except SpkApiError as exc:
            print(f"PARSE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    report = build_report(result)

    print(
        f"SPK IPO Applications | {raw.http_status} | {raw.content_type} | "
        f"{report['total_rows_scanned']} rows scanned | {report['valid_record_count']} valid | "
        f"{report['invalid_row_count']} invalid"
    )
    if report["earliest_application"]:
        e = report["earliest_application"]
        print(f"  earliest: {e['application_date']} — {e['company_name']}")
    if report["latest_application"]:
        l = report["latest_application"]
        print(f"  latest:   {l['application_date']} — {l['company_name']}")
    print(f"  duplicate company names: {report['duplicate_company_name_count']}")
    for name, dates in list(report["duplicate_company_names"].items())[:20]:
        print(f"    {name}: {dates}")
    print(f"  invalid rows: {report['invalid_row_count']}")
    for row in report["invalid_rows"][:20]:
        print(f"    row {row['row_index']}: {row['reason']} — {row['raw_row']}")

    # Normalized JSON: every valid record with an ISO application_date,
    # plus the raw row and invalid rows for full traceability.
    normalized_dest = args.report_dir / f"spk-ipo-applications-{run_timestamp}.json"
    normalized_payload = {
        "run_timestamp_utc": run_timestamp,
        "source_url": raw.requested_url,
        "report": report,
        "records": [
            {
                "company_name": r.company_name,
                "application_date": r.application_date.isoformat(),
                "application_date_raw": r.application_date_raw,
                "raw_row": list(r.raw_row),
            }
            for r in result.records
        ],
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    normalized_dest.write_text(json.dumps(normalized_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md_dest = args.report_dir / f"spk-ipo-applications-{run_timestamp}.md"
    md_lines = [
        f"# SPK IPO application list — {run_timestamp}",
        "",
        f"Source: {raw.requested_url}",
        "",
        f"- Total rows scanned: {report['total_rows_scanned']}",
        f"- Valid records: {report['valid_record_count']}",
        f"- Invalid rows: {report['invalid_row_count']}",
    ]
    if report["earliest_application"]:
        e = report["earliest_application"]
        md_lines.append(f"- Earliest application: {e['application_date']} — {e['company_name']}")
    if report["latest_application"]:
        l = report["latest_application"]
        md_lines.append(f"- Latest application: {l['application_date']} — {l['company_name']}")
    md_lines.append(f"- Duplicate company names: {report['duplicate_company_name_count']}")
    if report["duplicate_company_names"]:
        md_lines.append("")
        md_lines.append("| Company | Application dates |")
        md_lines.append("|---|---|")
        for name, dates in report["duplicate_company_names"].items():
            md_lines.append(f"| {name} | {', '.join(dates)} |")
    if report["invalid_rows"]:
        md_lines.append("")
        md_lines.append("| Row | Reason | Raw row |")
        md_lines.append("|---|---|---|")
        for row in report["invalid_rows"]:
            md_lines.append(f"| {row['row_index']} | {row['reason']} | {row['raw_row']} |")
    md_dest.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"\nRaw HTML saved to: {raw_dest}")
    print(f"Normalized JSON: {normalized_dest}")
    print(f"Markdown report: {md_dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
