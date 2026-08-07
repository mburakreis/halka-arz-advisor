#!/usr/bin/env python3
"""Gemini analysis MVP: turn a matched IPO's deterministic extracted facts
and cached KAP PDF text into a structured Turkish decision-support
summary, using the Gemini API.

Usage:
    uv run python scripts/analyze_pending_ipos.py
    uv run python scripts/analyze_pending_ipos.py --ticker QUICK

Requires ``GEMINI_API_KEY`` (see .env.example — get one from
https://aistudio.google.com/apikey). This command never downloads a KAP
document itself: it only analyzes documents an earlier
``uv run python scripts/fetch_kap_disclosures.py --parse-documents`` run
already cached under data/cache/kap_pdfs/. A company with no cached,
extractable PDF text is reported as insufficient_data, not skipped
silently.

A transient failure analyzing one company (Gemini rate limit, quota,
temporary server error) does not abort the run — that company is
skipped (nothing is cached for it, so it's retried on a later run) and
the rest continue. Only a total preflight failure (API unreachable, or
the configured model unavailable) stops the whole run.

This command never runs OCR itself — it only reads whatever
``fetch_kap_disclosures.py --ocr-scanned`` already cached under
data/cache/kap_ocr/ (see halka_arz_advisor.kap.ocr), same read-only
treatment as the PDF cache. No financial scoring formulas, Telegram
changes, or news monitoring happen here. GitHub Actions only invokes
this same script — no separate logic lives in the workflow.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.decision.pipeline import compute_decision_results, resolve_company_identity  # noqa: E402
from halka_arz_advisor.gemini.analysis import analyze_company, verify_gemini_ready  # noqa: E402
from halka_arz_advisor.gemini.cache import AnalysisCache  # noqa: E402
from halka_arz_advisor.gemini.client import GeminiClient  # noqa: E402
from halka_arz_advisor.gemini.config import load_gemini_config_from_env  # noqa: E402
from halka_arz_advisor.gemini.exceptions import GeminiError, GeminiUnavailableError  # noqa: E402
from halka_arz_advisor.gemini.models import AnalysisRecord  # noqa: E402
from halka_arz_advisor.kap.backfill import merge_backfilled_disclosures  # noqa: E402
from halka_arz_advisor.kap.backfill_cache import BackfillCache  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    aggregate_company_facts,
    process_disclosure_documents,
)
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

DEFAULT_ANALYSIS_CACHE_DIR = Path("data") / "cache" / "llm_analysis"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--days", type=int, default=30, help="How many days back to look for KAP disclosures (default: 30)"
    )
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Year to fetch completed SPK IPO records for, to match against (default: current year)",
    )
    parser.add_argument("--ticker", type=str, default=None, help="Only analyze this ticker (case-insensitive)")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--analysis-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_ANALYSIS_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--backfill-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "kap_backfill")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    return parser.parse_args(argv)


def _matches_ticker_filter(disclosure: KapDisclosure, ticker_filter: str) -> bool:
    wanted = ticker_filter.strip().upper()
    if disclosure.ticker and disclosure.ticker.upper() == wanted:
        return True
    if disclosure.matched_spk_record_id and disclosure.matched_spk_record_id.startswith(f"ipo:{wanted}:"):
        return True
    return wanted in disclosure.company_name.upper()

def _record_to_json(record: AnalysisRecord) -> dict:
    decision = record.decision_result
    return {
        "spk_record_id": record.spk_record_id,
        "llm_status": record.llm_status,
        "llm_model": record.llm_model,
        "llm_analysis": record.llm_analysis.as_dict() if record.llm_analysis is not None else None,
        "llm_warnings": list(record.llm_warnings),
        "analyzed_at": record.analyzed_at.isoformat(),
        "decision": {
            "signal": decision.signal,
            "total_score": decision.total_score,
            "confidence_score": decision.confidence_score,
            "rule_version": decision.rule_version,
            "weight_set_version": decision.weight_set_version,
        }
        if decision is not None
        else None,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv_if_present(args.env_file)

    try:
        gemini_config = load_gemini_config_from_env()
    except GeminiError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

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

    matched = [
        match_disclosure(d, ipo_records=ipo_records, application_records=application_records)
        for d in target_disclosures
    ]
    matched = [d for d in matched if d.match_method != "unmatched"]
    if args.ticker:
        matched = [d for d in matched if _matches_ticker_filter(d, args.ticker)]
    print(f"{len(matched)} matched target disclosure(s) to inspect for cached documents", file=sys.stderr)

    # cache_only=True: attachment metadata is still resolved live (a
    # small JSON call), but the PDF itself is only ever read from
    # pdf_cache — never (re-)downloaded here.
    pdf_cache = PdfCache(args.pdf_cache_dir)
    ocr_cache = OcrCache(args.ocr_cache_dir)
    processed = [
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True) for d in matched
    ]

    # Cheap, network-search-free: only re-attaches whatever an earlier
    # `scripts/backfill_kap_history.py` run already found and cached for
    # a matched company (see halka_arz_advisor.kap.backfill) — never
    # searches KAP's history itself here.
    processed = merge_backfilled_disclosures(
        processed,
        ipo_records=ipo_records,
        application_records=application_records,
        backfill_cache=BackfillCache(args.backfill_cache_dir),
        pdf_cache=pdf_cache,
        config=config,
        ocr_cache=ocr_cache,
    )

    company_facts = aggregate_company_facts(processed)
    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in processed:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    # The deterministic decision is computed once here, from exactly the
    # same cached data every analysis below explains — see
    # halka_arz_advisor.decision.pipeline's module docstring for why this
    # must be the *same* computation scripts/send_pending_analyses.py
    # later performs (their cache-key derivations must agree).
    decision_results = compute_decision_results(processed, ipo_records=tuple(ipo_records), application_records=tuple(application_records))

    # Only companies with both real ExtractedFacts *and* a computed
    # decision are analyzable — a company with disclosures but no
    # readable document (all scanned, no digital text) has neither.
    analyzable_record_ids = sorted(set(company_facts) & set(decision_results))

    if not analyzable_record_ids:
        print("No companies with cached, matched documents to analyze.", file=sys.stderr)
        print(json.dumps([], indent=2))
        return 0

    print(f"{len(analyzable_record_ids)} compan(y/ies) with cached documents ready for analysis", file=sys.stderr)

    print("Checking Gemini availability and configured model...", file=sys.stderr)
    gemini_client = GeminiClient(gemini_config)
    try:
        verify_gemini_ready(gemini_client)
    except GeminiError as exc:
        print(f"Gemini not ready: {type(exc).__name__}: {exc}", file=sys.stderr)
        gemini_client.close()
        now = datetime.now(UTC)
        records = [
            AnalysisRecord(
                spk_record_id=record_id,
                llm_status="model_unavailable",
                llm_model=gemini_config.model,
                llm_analysis=None,
                llm_warnings=(str(exc),),
                analyzed_at=now,
                decision_result=decision_results[record_id],
            )
            for record_id in analyzable_record_ids
        ]
        print(json.dumps([_record_to_json(r) for r in records], indent=2, ensure_ascii=False))
        return 1

    analysis_cache = AnalysisCache(args.analysis_cache_dir)
    results: list[AnalysisRecord] = []
    skipped_transient = 0
    for record_id in analyzable_record_ids:
        facts = company_facts[record_id]
        disclosures_for_company = disclosures_by_record.get(record_id, [])
        company_name, ticker = resolve_company_identity(
            record_id, disclosures_for_company, ipo_records=ipo_records, application_records=application_records
        )
        print(f"Analyzing {record_id} ({company_name})...", file=sys.stderr)
        try:
            record = analyze_company(
                spk_record_id=record_id,
                company_name=company_name,
                ticker=ticker,
                facts=facts,
                disclosures=disclosures_for_company,
                pdf_cache=pdf_cache,
                analysis_cache=analysis_cache,
                gemini_client=gemini_client,
                decision_result=decision_results[record_id],
                ocr_cache=ocr_cache,
            )
        except GeminiUnavailableError as exc:
            # Rate limit / quota / temporary server error for this one
            # company — nothing was cached, so it's simply retried on the
            # next (hourly) run. Does not abort the batch.
            print(f"  -> skipped (temporary Gemini error): {exc}", file=sys.stderr)
            skipped_transient += 1
            continue
        print(f"  -> {record.llm_status}", file=sys.stderr)
        results.append(record)

    gemini_client.close()

    status_counts: dict[str, int] = {}
    for record in results:
        status_counts[record.llm_status] = status_counts.get(record.llm_status, 0) + 1
    if skipped_transient:
        status_counts["skipped_transient"] = skipped_transient
    print(f"Status breakdown: {status_counts}", file=sys.stderr)

    print(json.dumps([_record_to_json(r) for r in results], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
