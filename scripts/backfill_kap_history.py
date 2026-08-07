#!/usr/bin/env python3
"""Historical KAP document backfill for matched IPOs with incomplete
decision coverage.

Usage:
    uv run python scripts/backfill_kap_history.py
    uv run python scripts/backfill_kap_history.py --ticker QUICK
    uv run python scripts/backfill_kap_history.py --year 2026

For every company with a matched SPK completed-IPO or application
record that's still missing one or more of the five supported KAP
document types (approved_prospectus, investor_sale_announcement,
price_determination_report, ipo_results, trading_start — see
halka_arz_advisor.kap.classification.target_document_types), this
searches further back in KAP history than the normal recent-disclosure
window, bounded to a reasonable IPO lifecycle window around that
company's own SPK application/completed-IPO dates (see
halka_arz_advisor.kap.backfill.lifecycle_window) — never unlimited
history.

Reuses the existing KAP matching, classification, PDF cache, OCR
fallback, and extraction pipeline unmodified; no new scoring,
extractors, Gemini behavior, or Telegram formatting happen here. What's
found (or exhaustively searched for and not found) is persisted under
data/cache/kap_backfill/ so a later run — including this same script run
hourly by CI — never repeats an already-exhausted historical search.

This command only *discovers and caches* historical documents; it does
not itself compute or print decision results (see
scripts/validate_decision_engine.py, whose consumer-script wiring
already merges in whatever this cached — see
halka_arz_advisor.kap.backfill.merge_backfilled_disclosures — before
recomputing the deterministic decision).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.kap.backfill import missing_document_types, search_and_backfill  # noqa: E402
from halka_arz_advisor.kap.backfill_cache import BackfillCache  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import DEFAULT_CACHE_DIR, process_disclosure_documents  # noqa: E402
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.ocr import DEFAULT_OCR_CACHE_DIR, OcrCache  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.notify.identity import application_identity, ipo_identity  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402

DEFAULT_BACKFILL_CACHE_DIR = Path("data") / "cache" / "kap_backfill"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=90, help="Recent-window lookback in days, to know what's already covered (default: 90)")
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Year to fetch completed SPK IPO records for (default: current year)",
    )
    parser.add_argument("--ticker", type=str, default=None, help="Only consider this ticker (case-insensitive)")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--backfill-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_BACKFILL_CACHE_DIR)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--ocr-scanned", action="store_true", help="Fall back to local OCR for a scanned/empty backfilled PDF")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv_if_present(args.env_file)
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

    matched = [match_disclosure(d, ipo_records=ipo_records, application_records=application_records) for d in target_disclosures]
    matched = [d for d in matched if d.match_method != "unmatched"]

    pdf_cache = PdfCache(args.pdf_cache_dir)
    ocr_cache = OcrCache(args.ocr_cache_dir)
    # Read-only for the recent window — this script's own network/PDF
    # cost is spent on the historical search below, not on re-reading
    # what scripts/fetch_kap_disclosures.py already cached.
    processed = [
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True, ocr_scanned=args.ocr_scanned, ocr_cache=ocr_cache)
        for d in matched
    ]

    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in processed:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    ipo_by_identity = {ipo_identity(r): r for r in ipo_records}
    application_by_identity = {application_identity(r): r for r in application_records}

    # Every company either already has a recent matched disclosure, or
    # is a matched SPK record on its own — a company with *zero* recent
    # KAP activity still deserves a backfill pass; that's exactly the
    # "incomplete decision coverage" case this command exists for.
    record_ids = set(disclosures_by_record) | set(ipo_by_identity) | set(application_by_identity)

    if args.ticker:
        wanted = args.ticker.strip().upper()
        record_ids = {
            rid
            for rid in record_ids
            if rid.startswith(f"ipo:{wanted}:")
            or (ipo_by_identity.get(rid) and (ipo_by_identity[rid].borsa_kodu or "").upper() == wanted)
        }

    print(f"{len(record_ids)} compan(y/ies) to consider for backfill", file=sys.stderr)

    backfill_cache = BackfillCache(args.backfill_cache_dir)
    reference_date = date.today()

    results = []
    stopped_early_reason: str | None = None
    with KapClient(config) as kap_client:
        for record_id in sorted(record_ids):
            current = disclosures_by_record.get(record_id, [])
            before = missing_document_types(current)

            try:
                outcome = search_and_backfill(
                    record_id,
                    ipo_record=ipo_by_identity.get(record_id),
                    application_record=application_by_identity.get(record_id),
                    current_disclosures=current,
                    ipo_records=ipo_records,
                    application_records=application_records,
                    cache=backfill_cache,
                    kap_client=kap_client,
                    pdf_cache=pdf_cache,
                    config=config,
                    ocr_scanned=args.ocr_scanned,
                    ocr_cache=ocr_cache,
                    reference_date=reference_date,
                )
            except KapApiError as exc:
                # A persistent failure (e.g. HTTP 429 after this
                # project's own bounded retry/backoff already ran) most
                # likely means every subsequent request would fail the
                # same way — stop here rather than hammer the endpoint
                # further. Every record_id processed before this one
                # already had its BackfillEntry persisted individually
                # (search_and_backfill saves per-company, not in one
                # final batch), so that progress is not lost; only the
                # remaining, not-yet-attempted companies are skipped
                # this run and will be retried on the next one.
                print(f"  {record_id}: FAILED ({type(exc).__name__}: {exc}) — stopping this run, prior progress preserved", file=sys.stderr)
                stopped_early_reason = f"{type(exc).__name__}: {exc}"
                break

            after = missing_document_types(list(current) + list(outcome.disclosures))
            results.append(
                {
                    "record_id": record_id,
                    "searched": outcome.searched,
                    "window": [outcome.window[0].isoformat(), outcome.window[1].isoformat()] if outcome.window else None,
                    "recovered_document_types": list(outcome.recovered_document_types),
                    "missing_before": list(before),
                    "missing_after": list(after),
                    "backfilled_disclosure_count": len(outcome.disclosures),
                }
            )
            if outcome.searched:
                print(
                    f"  {record_id}: searched {outcome.window[0]}..{outcome.window[1]}, "
                    f"recovered {outcome.recovered_document_types or '(nothing)'}",
                    file=sys.stderr,
                )

    print(json.dumps({"companies": results, "stopped_early_reason": stopped_early_reason}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
