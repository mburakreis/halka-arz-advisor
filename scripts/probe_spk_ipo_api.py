#!/usr/bin/env python3
"""Phase 1A CLI: discover SPK's IPO endpoint from its OpenAPI document,
fetch IPO records for one or more years, and report what's there.

Usage:
    uv run python scripts/probe_spk_ipo_api.py --year 2026
    uv run python scripts/probe_spk_ipo_api.py --year 2026 --year 2025 --year 2024 --year 1990
    uv run python scripts/probe_spk_ipo_api.py --check-ordering-year 2024

This does not build a business-domain IPO model, does not scrape KAP,
and does not use a browser — it only discovers, fetches, validates, and
reports on the one documented SPK IPO data endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.exceptions import (  # noqa: E402
    SpkApiError,
    SpkDiscoveryError,
    SpkResponseError,
    SpkSchemaError,
    SpkTransportError,
)
from halka_arz_advisor.spk.ipo_client import SpkIpoApiClient, SpkIpoApiResult  # noqa: E402
from halka_arz_advisor.spk.profiler import (  # noqa: E402
    OrderingComparison,
    RecordShapeProfile,
    compare_ordering,
    profile_records,
)

_DEFAULTS = ProbeConfig()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--year", type=int, action="append", default=None,
        help="Year to fetch IPO records for. Repeatable, e.g. --year 2025 --year 2024.",
    )
    parser.add_argument(
        "--check-ordering-year", type=int, default=None,
        help="If given, fetch this year twice back-to-back and report whether record ordering was stable.",
    )
    parser.add_argument("--delay", type=float, default=_DEFAULTS.delay_between_requests_seconds)
    parser.add_argument("--connect-timeout", type=float, default=_DEFAULTS.connect_timeout_seconds)
    parser.add_argument("--read-timeout", type=float, default=_DEFAULTS.read_timeout_seconds)
    parser.add_argument("--max-retries", type=int, default=_DEFAULTS.max_retries)
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data" / "raw")
    parser.add_argument("--report-dir", type=Path, default=PROJECT_ROOT / "data" / "probe-results")
    parser.add_argument("--docs-path", type=Path, default=PROJECT_ROOT / "docs" / "spk-ipo-api.md")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not args.year and args.check_ordering_year is None:
        parser.error("pass at least one --year or --check-ordering-year")
    return args


def _run_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _schema_validation_to_dict(validation) -> dict:
    return {
        "ok": validation.ok,
        "documented_fields": list(validation.documented_fields),
        "observed_fields": sorted(validation.observed_fields),
        "undocumented_fields": sorted(validation.undocumented_fields),
        "fields_never_observed": sorted(validation.fields_never_observed),
        "issue_count": len(validation.issues),
        "issues": [asdict(i) for i in validation.issues[:200]],
    }


def _profile_to_dict(profile: RecordShapeProfile) -> dict:
    return {
        "record_count": profile.record_count,
        "all_observed_keys": sorted(profile.all_observed_keys),
        "keys_missing_from_some_records": sorted(profile.keys_missing_from_some_records),
        "keys_always_null": sorted(profile.keys_always_null),
        "observed_types_per_key": {k: sorted(v) for k, v in profile.observed_types_per_key.items()},
        "duplicate_full_records": [list(g) for g in profile.duplicate_full_records],
        "duplicate_identity_candidates": [asdict(c) for c in profile.duplicate_identity_candidates],
        "first_record": profile.first_record,
        "last_record": profile.last_record,
    }


def save_year_outputs(raw_dir: Path, year: int, run_timestamp: str, result: SpkIpoApiResult) -> Path:
    dest = raw_dir / "spk_ipo_api" / str(year) / run_timestamp
    dest.mkdir(parents=True, exist_ok=True)
    _write_json(dest / "response.json", result.raw_json)
    _write_json(
        dest / "meta.json",
        {
            "year": result.year,
            "requested_url": result.requested_url,
            "http_status": result.http_status,
            "content_type": result.content_type,
            "elapsed_ms": result.elapsed_ms,
            "record_count": result.record_count,
        },
    )
    _write_json(dest / "schema_validation.json", _schema_validation_to_dict(result.schema_validation))
    return dest


def save_error_outputs(raw_dir: Path, year: int, run_timestamp: str, error: Exception) -> Path:
    dest = raw_dir / "spk_ipo_api" / str(year) / run_timestamp
    dest.mkdir(parents=True, exist_ok=True)
    _write_json(dest / "meta.json", {"year": year, "error": f"{type(error).__name__}: {error}"})
    return dest


def format_year_status_line(year: int, result: SpkIpoApiResult | None, error: Exception | None) -> str:
    if error is not None:
        return f"SPK IPO API | {year} | ERROR | {type(error).__name__}: {error}"
    assert result is not None
    schema_status = "OK" if result.schema_validation.ok else f"ISSUES({len(result.schema_validation.issues)})"
    return (
        f"SPK IPO API | {year} | {result.http_status} | {result.content_type} | "
        f"{result.record_count} records | schema {schema_status}"
    )


def render_docs_markdown(
    client: SpkIpoApiClient,
    year_results: dict[int, SpkIpoApiResult],
    year_errors: dict[int, Exception],
    run_timestamp: str,
) -> str:
    schema = client.schema
    operation = client.operation

    lines = [
        "# SPK IPO API (Phase 1A, OpenAPI-discovered)",
        "",
        "Generated by `scripts/probe_spk_ipo_api.py` from the live OpenAPI document — "
        "field list and types below were read out of the document, not hand-written.",
        "",
        "## Endpoint",
        "",
        f"- Official Swagger URL: `https://ws.spk.gov.tr/swagger/v2/swagger.json`",
        f"- Method + path: `{operation.method} {operation.path}`",
        f"- Base URL (resolved from the OpenAPI document): `{client.base_url}`",
        f"- Full endpoint: `{client.endpoint_url}`",
        f"- Summary: {operation.summary or '(none documented)'}",
        f"- Tags: {', '.join(operation.tags) or '(none)'}",
        f"- Matched as IPO-related because: {'; '.join(operation.match_reasons) or '(n/a)'}",
        f"- Response content types documented: {', '.join(operation.response_content_types) or '(none)'}",
        f"- Security/authentication documented: "
        + (", ".join(str(s) for s in operation.security) if operation.security else "none documented"),
        "",
        "## Query parameters",
        "",
        "| Name | Location | Required | Type | Format | Description |",
        "|---|---|---|---|---|---|",
    ]
    for p in operation.parameters:
        lines.append(
            f"| `{p.name}` | {p.location} | {p.required} | {p.type or '-'} | {p.format or '-'} | "
            f"{p.description or '(none documented)'} |"
        )

    lines += [
        "",
        f"## Response schema: `{schema.name}`",
        "",
        f"- Resolved from: `{schema.ref}`",
        f"- `additionalProperties`: {'allowed' if schema.additional_properties_allowed else 'false (strict)'}",
        "",
        "| Field | Type | Format | Nullable | Required (schema `required` list) | Description |",
        "|---|---|---|---|---|---|",
    ]
    for f in schema.fields:
        required = "Yes" if f.required else "Not specified"
        lines.append(
            f"| `{f.name}` | {f.type or '-'} | {f.format or '-'} | {f.nullable} | {required} | "
            f"{f.description or '(none documented)'} |"
        )

    lines += ["", "## Live validation", "", f"- Date: {run_timestamp} (UTC)", ""]

    if not year_results and not year_errors:
        lines.append("No live requests were made in this run.")
    else:
        lines.append("| Year | Outcome | HTTP | Records | Schema |")
        lines.append("|---|---|---|---|---|")
        for year in sorted(set(year_results) | set(year_errors)):
            if year in year_errors:
                lines.append(f"| {year} | ERROR | - | - | {type(year_errors[year]).__name__} |")
            else:
                r = year_results[year]
                schema_status = "OK" if r.schema_validation.ok else f"{len(r.schema_validation.issues)} issue(s)"
                lines.append(f"| {year} | OK | {r.http_status} | {r.record_count} | {schema_status} |")

    lines += ["", "## Discrepancies between the documented schema and live responses", ""]
    all_undocumented: set[str] = set()
    all_never_observed: set[str] = set()
    type_mismatch_fields: set[str] = set()
    for r in year_results.values():
        all_undocumented |= r.schema_validation.undocumented_fields
        all_never_observed |= r.schema_validation.fields_never_observed
        for issue in r.schema_validation.issues:
            if issue.issue == "type_mismatch":
                type_mismatch_fields.add(issue.field_name)

    if not year_results:
        lines.append("No successful live response was available to compare against the schema.")
    elif not all_undocumented and not all_never_observed and not type_mismatch_fields:
        lines.append("None observed: every tested year's records matched the documented field set and types.")
    else:
        if all_undocumented:
            lines.append(f"- Fields observed in responses but **not** in the documented schema: {sorted(all_undocumented)}")
        if all_never_observed:
            lines.append(f"- Documented fields **never observed** (across all tested years): {sorted(all_never_observed)}")
        if type_mismatch_fields:
            lines.append(f"- Fields observed with a type that didn't match the schema: {sorted(type_mismatch_fields)}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_timestamp = _run_timestamp()

    config = ProbeConfig(
        delay_between_requests_seconds=args.delay,
        connect_timeout_seconds=args.connect_timeout,
        read_timeout_seconds=args.read_timeout,
        max_retries=args.max_retries,
    )

    print("Discovering SPK IPO endpoint from the live OpenAPI document...")
    openapi_raw_dir = args.raw_dir / "spk_openapi" / run_timestamp
    try:
        client = SpkIpoApiClient.discover(config, save_raw_openapi_to=openapi_raw_dir)
    except SpkApiError as exc:
        print(f"DISCOVERY FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    op = client.operation
    print(f"  Matched: {op.method} {op.path}  (\"{op.summary}\")")
    print(f"  Reasons: {'; '.join(op.match_reasons)}")
    print(f"  Base URL: {client.base_url}")
    print(f"  Response schema: {client.schema.name} ({len(client.schema.fields)} fields)")
    print(f"  Security: {'none documented' if not op.security else op.security}")
    print(f"  Raw OpenAPI document saved to: {openapi_raw_dir}")

    year_results: dict[int, SpkIpoApiResult] = {}
    year_errors: dict[int, Exception] = {}

    years = list(dict.fromkeys(args.year or []))
    for i, year in enumerate(years):
        if i > 0:
            time.sleep(config.delay_between_requests_seconds)
        try:
            result = client.fetch(year)
        except (SpkTransportError, SpkResponseError, SpkSchemaError) as exc:
            year_errors[year] = exc
            save_error_outputs(args.raw_dir, year, run_timestamp, exc)
            print(format_year_status_line(year, None, exc))
            continue
        year_results[year] = result
        save_year_outputs(args.raw_dir, year, run_timestamp, result)
        print(format_year_status_line(year, result, None))

    ordering: OrderingComparison | None = None
    if args.check_ordering_year is not None:
        year = args.check_ordering_year
        print(f"Checking ordering stability for year {year} (two consecutive requests)...")
        try:
            first = client.fetch(year)
            time.sleep(max(config.delay_between_requests_seconds, 0.5))
            second = client.fetch(year)
        except (SpkTransportError, SpkResponseError, SpkSchemaError) as exc:
            print(f"  ordering check FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            dest_a = save_year_outputs(args.raw_dir, year, run_timestamp + "-ordering-a", first)
            dest_b = save_year_outputs(args.raw_dir, year, run_timestamp + "-ordering-b", second)
            ordering = compare_ordering(first.raw_json, second.raw_json)
            print(f"  fetch 1: {format_year_status_line(year, first, None)}")
            print(f"  fetch 2: {format_year_status_line(year, second, None)}")
            print(f"  ordering stable: {ordering.stable} ({ordering.reason})")
            print(f"  raw saved to: {dest_a} and {dest_b}")
            if year not in year_results:
                year_results[year] = second

    client.close()

    profiles = {year: profile_records(r.raw_json) for year, r in year_results.items()}
    for year, profile in profiles.items():
        print(
            f"  profile[{year}]: {profile.record_count} records, {len(profile.all_observed_keys)} keys, "
            f"{len(profile.keys_always_null)} always-null, "
            f"{len(profile.duplicate_full_records)} duplicate-record group(s)"
        )

    report_payload = {
        "run_timestamp_utc": run_timestamp,
        "openapi_document_url": "https://ws.spk.gov.tr/swagger/v2/swagger.json",
        "matched_operation": {
            "method": op.method,
            "path": op.path,
            "summary": op.summary,
            "tags": list(op.tags),
            "match_reasons": list(op.match_reasons),
            "response_content_types": list(op.response_content_types),
            "security": list(op.security),
        },
        "base_url": client.base_url,
        "schema_name": client.schema.name,
        "schema_field_count": len(client.schema.fields),
        "years": {
            str(year): {
                "outcome": "error" if year in year_errors else "ok",
                "error": f"{type(year_errors[year]).__name__}: {year_errors[year]}" if year in year_errors else None,
                "http_status": year_results[year].http_status if year in year_results else None,
                "record_count": year_results[year].record_count if year in year_results else None,
                "schema_validation": _schema_validation_to_dict(year_results[year].schema_validation)
                if year in year_results
                else None,
                "profile": _profile_to_dict(profiles[year]) if year in profiles else None,
            }
            for year in sorted(set(year_results) | set(year_errors))
        },
        "ordering_check": (
            {
                "year": args.check_ordering_year,
                "stable": ordering.stable,
                "same_length": ordering.same_length,
                "reason": ordering.reason,
            }
            if ordering is not None
            else None
        ),
    }
    json_report_path = args.report_dir / f"spk-ipo-api-report-{run_timestamp}.json"
    _write_json(json_report_path, report_payload)

    md_report_path = args.report_dir / f"spk-ipo-api-report-{run_timestamp}.md"
    md_lines = [f"# SPK IPO API probe report — {run_timestamp}", "", f"Endpoint: `{client.endpoint_url}`", ""]
    md_lines.append("| Year | Outcome | HTTP | Records | Schema |")
    md_lines.append("|---|---|---|---|---|")
    for year in sorted(set(year_results) | set(year_errors)):
        if year in year_errors:
            md_lines.append(f"| {year} | ERROR | - | - | {type(year_errors[year]).__name__} |")
        else:
            r = year_results[year]
            schema_status = "OK" if r.schema_validation.ok else f"{len(r.schema_validation.issues)} issue(s)"
            md_lines.append(f"| {year} | OK | {r.http_status} | {r.record_count} | {schema_status} |")
    if ordering is not None:
        md_lines += ["", f"Ordering check (year {args.check_ordering_year}): stable={ordering.stable} — {ordering.reason}"]
    args.report_dir.mkdir(parents=True, exist_ok=True)
    md_report_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"\nJSON report: {json_report_path}")
    print(f"Markdown report: {md_report_path}")

    docs_markdown = render_docs_markdown(client, year_results, year_errors, run_timestamp)
    args.docs_path.parent.mkdir(parents=True, exist_ok=True)
    args.docs_path.write_text(docs_markdown, encoding="utf-8")
    print(f"Docs updated: {args.docs_path}")

    if year_errors:
        print(f"\n{len(year_errors)} of {len(years)} year(s) failed with a hard error.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
