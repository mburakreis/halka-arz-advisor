#!/usr/bin/env python3
"""Deterministic decision-engine validation report for cached IPO data.

Usage:
    uv run python scripts/validate_decision_engine.py
    uv run python scripts/validate_decision_engine.py --ticker QUICK

Runs halka_arz_advisor.decision.engine.evaluate_decision (via
halka_arz_advisor.decision.pipeline.compute_decision_results) for every
matched company with cached KAP/SPK data and prints a deterministic JSON
report to stdout: ticker/company, signal, total score, confidence,
category scores + coverage, hard rules, warnings, unavailable high-
weight features, and the top positive/negative feature contributions.

Reuses the exact same fetch-and-match pipeline as
scripts/audit_decision_coverage.py, in cache-only mode — this command
never downloads a KAP document and never runs OCR itself. No scoring
changes happen here; this is read-only inspection of the already-built
expert_v0 engine.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.decision.engine import (  # noqa: E402
    top_negative_contributions,
    top_positive_contributions,
    unavailable_high_weight_features,
)
from halka_arz_advisor.decision.pipeline import compute_decision_results, resolve_company_identity  # noqa: E402
from halka_arz_advisor.kap.backfill import merge_backfilled_disclosures  # noqa: E402
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
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=90, help="How many days back to look for KAP disclosures (default: 90)")
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Year to fetch completed SPK IPO records for, to match against (default: current year)",
    )
    parser.add_argument("--ticker", type=str, default=None, help="Only evaluate this ticker (case-insensitive)")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--backfill-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "kap_backfill")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--min-coverage", type=float, default=0.0, help="Only include companies with at least one category above this weighted coverage (default: 0.0 = include all)")
    parser.add_argument(
        "--no-backfill", action="store_true",
        help="Skip merging in previously backfilled historical documents (see scripts/backfill_kap_history.py) — recent-window data only",
    )
    return parser.parse_args(argv)


def _matches_ticker_filter(disclosure: KapDisclosure, ticker_filter: str) -> bool:
    wanted = ticker_filter.strip().upper()
    if disclosure.ticker and disclosure.ticker.upper() == wanted:
        return True
    if disclosure.matched_spk_record_id and disclosure.matched_spk_record_id.startswith(f"ipo:{wanted}:"):
        return True
    return wanted in disclosure.company_name.upper()


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
    if args.ticker:
        matched = [d for d in matched if _matches_ticker_filter(d, args.ticker)]
    print(f"{len(matched)} matched target disclosure(s) to inspect for cached documents", file=sys.stderr)

    pdf_cache = PdfCache(args.pdf_cache_dir)
    ocr_cache = OcrCache(args.ocr_cache_dir)
    processed = [
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True, ocr_scanned=True, ocr_cache=ocr_cache)
        for d in matched
    ]

    if not args.no_backfill:
        # Cheap, network-search-free: only re-attaches whatever an
        # earlier `scripts/backfill_kap_history.py` run already found
        # and cached — never searches KAP's history itself here.
        processed = merge_backfilled_disclosures(
            processed,
            ipo_records=ipo_records,
            application_records=application_records,
            backfill_cache=BackfillCache(args.backfill_cache_dir),
            pdf_cache=pdf_cache,
            config=config,
            ocr_scanned=True,
            ocr_cache=ocr_cache,
        )

    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in processed:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    if not disclosures_by_record:
        print("No companies with cached, matched documents to evaluate.", file=sys.stderr)
        print(json.dumps({"companies": []}, indent=2))
        return 0

    reference_date = datetime.now()
    decision_results = compute_decision_results(
        processed, ipo_records=tuple(ipo_records), application_records=tuple(application_records), reference_date=reference_date
    )
    print(f"{len(decision_results)} compan(y/ies) evaluated by the decision engine", file=sys.stderr)

    companies_output = []
    for record_id, result in sorted(decision_results.items()):
        disclosures_for_company = disclosures_by_record.get(record_id, [])
        company_name, ticker = resolve_company_identity(
            record_id, disclosures_for_company, ipo_records=ipo_records, application_records=application_records
        )

        max_coverage = max((c.coverage for c in result.category_scores), default=0.0)
        if max_coverage < args.min_coverage:
            continue

        companies_output.append(
            {
                "spk_record_id": record_id,
                "ticker": ticker,
                "company_name": company_name,
                "signal": result.signal,
                "total_score": result.total_score,
                "confidence_score": result.confidence_score,
                "category_scores": [
                    {"category": c.category, "score": c.score, "coverage": c.coverage, "status": c.status}
                    for c in result.category_scores
                ],
                "hard_rules": [
                    {"rule_id": r.rule_id, "target": r.target, "triggered": r.triggered, "reason": r.reason}
                    for r in result.hard_rules
                ],
                "warnings": list(result.warnings),
                "unavailable_high_weight_features": [
                    {"feature_id": c.feature_id, "category": c.category, "weight": c.weight, "status": c.status}
                    for c in unavailable_high_weight_features(result)
                ],
                "top_positive_contributions": [
                    {"feature_id": c.feature_id, "category": c.category, "score": c.normalized_score, "weight": c.weight}
                    for c in top_positive_contributions(result)
                ],
                "top_negative_contributions": [
                    {"feature_id": c.feature_id, "category": c.category, "score": c.normalized_score, "weight": c.weight}
                    for c in top_negative_contributions(result)
                ],
            }
        )
        print(f"  {ticker or record_id} ({company_name}): {result.signal}, total={result.total_score}, confidence={result.confidence_score:.1f}", file=sys.stderr)

    print(json.dumps({"companies": companies_output}, indent=2, ensure_ascii=False, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
