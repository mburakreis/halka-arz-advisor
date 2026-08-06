#!/usr/bin/env python3
"""Narrow, one-off diagnostic for the table(s) on SPK's IPO application page.

Phase 0 detected exactly one <table> on
https://spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu, and
three "download-looking" links that turned out to be generic SPK
publications (a disclosure PDF, a strategic plan PDF, an annual-reports
listing page) rather than confirmed IPO-specific documents.

This script only inspects and reports on that table's shape — headers,
row count, first/last rows — so a human can judge whether it's useful
IPO application data. It is NOT a production parser: it doesn't extract
or normalize rows into any model.

Usage:
    uv run python scripts/inspect_spk_application_table.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import httpx  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.probe.http_client import build_client, fetch_with_retry  # noqa: E402

URL = "https://spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu"

IPO_TABLE_KEYWORDS = (
    "halka arz", "başvuru", "şirket", "unvan", "tarih", "pazar", "aracı kurum", "borsa",
)


def _run_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _row_cell_texts(row) -> list[str]:
    cells = row.find_all(["td", "th"])
    return [c.get_text(strip=True) for c in cells]


def _table_headers(table) -> list[str]:
    thead = table.find("thead")
    if thead:
        header_row = thead.find("tr")
        if header_row:
            return _row_cell_texts(header_row)
    first_row = table.find("tr")
    if first_row and first_row.find("th"):
        return _row_cell_texts(first_row)
    return []


def _table_data_rows(table) -> list:
    thead = table.find("thead")
    header_row = thead.find("tr") if thead else None
    rows = table.find_all("tr")
    data_rows = []
    for row in rows:
        if row is header_row:
            continue
        if row.find("th") and not row.find("td"):
            # header-only row not wrapped in <thead>
            continue
        data_rows.append(row)
    return data_rows


def _looks_like_ipo_table(headers: list[str], sample_rows: list[list[str]]) -> tuple[bool, list[str]]:
    haystack = " ".join(headers).lower() + " " + " ".join(" ".join(r) for r in sample_rows).lower()
    matched = [kw for kw in IPO_TABLE_KEYWORDS if kw in haystack]
    return (len(matched) >= 2, matched)


def inspect(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    results = []
    for i, table in enumerate(tables):
        headers = _table_headers(table)
        data_rows = _table_data_rows(table)
        row_texts = [_row_cell_texts(r) for r in data_rows]
        first_three = row_texts[:3]
        last_three = row_texts[-3:] if len(row_texts) > 3 else []
        looks_like_ipo, matched_keywords = _looks_like_ipo_table(headers, first_three + last_three)
        results.append(
            {
                "table_index": i,
                "headers": headers,
                "row_count": len(data_rows),
                "first_three_rows": first_three,
                "last_three_rows": last_three,
                "looks_like_ipo_application_table": looks_like_ipo,
                "matched_keywords": matched_keywords,
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data" / "raw")
    parser.add_argument("--delay", type=float, default=ProbeConfig().delay_between_requests_seconds)
    args = parser.parse_args(argv)

    config = ProbeConfig(delay_between_requests_seconds=args.delay)
    run_timestamp = _run_timestamp()

    print(f"Fetching {URL} ...")
    with build_client(config) as client:
        try:
            response = fetch_with_retry(client, URL, config, headers={"Accept": "text/html"})
        except httpx.TransportError as exc:
            print(f"FAILED: transport error: {exc}", file=sys.stderr)
            return 1

    if response.status_code >= 400:
        print(f"FAILED: HTTP {response.status_code}", file=sys.stderr)
        return 1

    dest = args.raw_dir / "spk_application_table" / run_timestamp
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "response.html").write_bytes(response.content)

    results = inspect(response.text)
    (dest / "table_summary.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"HTTP {response.status_code}, {len(response.content)} bytes, {len(results)} table(s) found\n")
    for t in results:
        verdict = "LIKELY the IPO application table" if t["looks_like_ipo_application_table"] else "NOT clearly IPO-specific"
        print(f"Table {t['table_index']}: {t['row_count']} data row(s) — {verdict}")
        print(f"  headers: {t['headers']}")
        print(f"  matched keywords: {t['matched_keywords']}")
        print("  first 3 rows:")
        for row in t["first_three_rows"]:
            print(f"    {row}")
        print("  last 3 rows:")
        for row in t["last_three_rows"]:
            print(f"    {row}")
        print()

    print(f"Raw HTML + summary saved to: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
