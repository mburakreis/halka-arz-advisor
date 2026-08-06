#!/usr/bin/env python3
"""KAP IPO-disclosure diagnostic: fetch recent KAP disclosures, classify
them, match them against existing SPK records, optionally resolve their
attachments and extract IPO participation fields from the primary PDF,
and print the normalized results as JSON.

Usage:
    uv run python scripts/fetch_kap_disclosures.py
    uv run python scripts/fetch_kap_disclosures.py --days 30 --year 2026
    uv run python scripts/fetch_kap_disclosures.py --days 30 --year 2026 --parse-documents
    uv run python scripts/fetch_kap_disclosures.py --year 2026 --ticker QUICK --parse-documents

Progress/diagnostics go to stderr; the normalized JSON goes to stdout
only, so `... > out.json` captures just the data.

``--parse-documents`` resolves attachments and downloads/reads the
primary PDF for every *matched* target-type disclosure (see
halka_arz_advisor.kap.documents) — not every disclosure, to avoid
downloading dozens of PDFs nobody asked about; combine with ``--ticker``
to restrict this to one company. No OCR, scoring, recommendations, or
Telegram notifications happen here — this is read-only ingestion,
matching, and deterministic text extraction only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import DEFAULT_CACHE_DIR, aggregate_company_facts, process_disclosure_documents  # noqa: E402
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.extraction import ExtractedFacts, FIELD_NAMES  # noqa: E402
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--days", type=int, default=30, help="How many days back to fetch KAP disclosures for (default: 30)"
    )
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Year to fetch completed SPK IPO records for, to match against (default: current year)",
    )
    parser.add_argument(
        "--ticker", type=str, default=None,
        help="Only process disclosures for this ticker (case-insensitive) — matched against the "
        "disclosure's own ticker or its matched SPK record. Combine with --parse-documents to test "
        "one IPO without processing every disclosure found.",
    )
    parser.add_argument(
        "--parse-documents", action="store_true",
        help="Resolve attachments and download/read the primary PDF for every matched target-type "
        "disclosure, extracting IPO participation fields where the document type supports it.",
    )
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    return parser.parse_args(argv)


def _matches_ticker_filter(disclosure: KapDisclosure, ticker_filter: str) -> bool:
    wanted = ticker_filter.strip().upper()
    if disclosure.ticker and disclosure.ticker.upper() == wanted:
        return True
    if disclosure.matched_spk_record_id and disclosure.matched_spk_record_id.startswith(f"ipo:{wanted}:"):
        return True
    return wanted in disclosure.company_name.upper()


def _json_default(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _extracted_facts_to_json(facts: ExtractedFacts | None) -> dict | None:
    if facts is None:
        return None
    result = {}
    for field_name in FIELD_NAMES:
        fact = getattr(facts, field_name)
        result[field_name] = {
            "status": fact.status,
            "value": fact.value,
            "raw_snippet": fact.raw_snippet,
            "source": asdict(fact.source) if fact.source else None,
            "observations": [
                {"value": obs.value, "raw_snippet": obs.raw_snippet, "source": asdict(obs.source)}
                for obs in fact.observations
            ],
        }
    return result


def _disclosure_to_json(d: KapDisclosure) -> dict:
    return {
        "disclosure_id": d.disclosure_id,
        "published_at": d.published_at.isoformat(),
        "company_name": d.company_name,
        "ticker": d.ticker,
        "title": d.title,
        "summary": d.summary,
        "document_type": d.document_type,
        "notification_url": d.notification_url,
        "attachment_urls": list(d.attachment_urls),
        "matched_spk_record_id": d.matched_spk_record_id,
        "match_method": d.match_method,
        "attachments": [
            {"name": a.name, "url": a.url, "content_type": a.content_type, "document_role": a.document_role}
            for a in d.attachments
        ],
        "primary_document": (
            {
                "name": d.primary_document.name,
                "url": d.primary_document.url,
                "content_type": d.primary_document.content_type,
                "document_role": d.primary_document.document_role,
            }
            if d.primary_document
            else None
        ),
        "pdf_status": d.pdf_status,
        "extracted_facts": _extracted_facts_to_json(d.extracted_facts),
        "extraction_warnings": list(d.extraction_warnings),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = ProbeConfig()

    print(f"Fetching KAP disclosures from the last {args.days} day(s)...", file=sys.stderr)
    try:
        with KapClient(config) as kap_client:
            disclosures = kap_client.fetch_recent_disclosures(days=args.days)
    except KapApiError as exc:
        print(f"FAILED to fetch KAP disclosures: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    targets = set(target_document_types())
    target_disclosures = [d for d in disclosures if d.document_type in targets]
    print(
        f"{len(disclosures)} disclosure(s) fetched, {len(target_disclosures)} match a target IPO document type "
        f"(analyst/broker reviews of the price report are classified separately and excluded here)",
        file=sys.stderr,
    )

    print(f"Fetching SPK records for matching (completed IPOs for {args.year} + applications)...", file=sys.stderr)
    try:
        with SpkApiClient(config) as spk_client:
            ipo_records = spk_client.get_initial_public_offerings(args.year)
    except SpkApiError as exc:
        print(f"FAILED to fetch SPK IPO records: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        with SpkApplicationListClient(config) as application_client:
            application_records = application_client.get_applications()
    except SpkApiError as exc:
        print(f"FAILED to fetch SPK application records: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    matched = [
        match_disclosure(d, ipo_records=ipo_records, application_records=application_records)
        for d in target_disclosures
    ]

    if args.ticker:
        before = len(matched)
        matched = [d for d in matched if _matches_ticker_filter(d, args.ticker)]
        print(f"--ticker {args.ticker}: {len(matched)} of {before} target disclosure(s) kept", file=sys.stderr)

    matched_count = sum(1 for d in matched if d.match_method != "unmatched")
    by_method: dict[str, int] = {}
    for d in matched:
        by_method[d.match_method] = by_method.get(d.match_method, 0) + 1
    print(
        f"{matched_count} of {len(matched)} target disclosure(s) matched to an SPK record ({by_method})",
        file=sys.stderr,
    )

    if args.parse_documents:
        cache = PdfCache(args.cache_dir)
        to_process = [d for d in matched if d.match_method != "unmatched"]
        print(f"Resolving attachments and parsing PDFs for {len(to_process)} matched disclosure(s)...", file=sys.stderr)
        processed_by_id = {}
        for i, d in enumerate(to_process):
            if i > 0:
                time.sleep(config.delay_between_requests_seconds)
            try:
                processed_by_id[d.disclosure_id] = process_disclosure_documents(d, config=config, cache=cache)
            except KapApiError as exc:
                print(
                    f"  WARNING: failed to process documents for {d.disclosure_id} ({d.company_name}): "
                    f"{type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        matched = [processed_by_id.get(d.disclosure_id, d) for d in matched]

        pdf_status_counts: dict[str, int] = {}
        for d in matched:
            if d.pdf_status:
                pdf_status_counts[d.pdf_status] = pdf_status_counts.get(d.pdf_status, 0) + 1
        print(f"PDF status breakdown: {pdf_status_counts}", file=sys.stderr)

        company_facts = aggregate_company_facts(matched)
        output = {
            "disclosures": [_disclosure_to_json(d) for d in matched],
            "company_facts": {
                record_id: _extracted_facts_to_json(facts) for record_id, facts in company_facts.items()
            },
        }
        print(json.dumps(output, indent=2, ensure_ascii=False, default=_json_default))
        return 0

    print(json.dumps([_disclosure_to_json(d) for d in matched], indent=2, ensure_ascii=False, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
