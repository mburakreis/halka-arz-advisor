#!/usr/bin/env python3
"""Telegram delivery for completed Gemini IPO analyses.

Usage:
    uv run python scripts/send_pending_analyses.py
    uv run python scripts/send_pending_analyses.py --ticker QUICK
    uv run python scripts/send_pending_analyses.py --dry-run

Reads whatever ``scripts/analyze_pending_ipos.py`` has already cached
under ``data/cache/llm_analysis/`` (this command never calls Gemini
itself) and sends one concise Telegram message per company whose
analysis is ``completed`` or ``insufficient_data`` and hasn't already
been sent unchanged — see ``halka_arz_advisor.notify.analysis_identity``
for the dedup hash and ``halka_arz_advisor.notify.analysis_state`` for
where "already sent" is tracked (a dedicated state file, separate from
the SPK new-IPO notification state).

``--dry-run`` prints what would be sent to stdout without contacting
Telegram or touching the state file — safe to run repeatedly.

A single company's Telegram delivery failure doesn't abort the run:
that company just isn't marked sent, so it's retried on the next run.

No OCR, subscription-date reminders, scoring formulas, news monitoring,
or new LLM providers here — delivery only.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.decision.pipeline import compute_decision_results  # noqa: E402
from halka_arz_advisor.gemini.cache import AnalysisCache  # noqa: E402
from halka_arz_advisor.gemini.config import DEFAULT_MODEL  # noqa: E402
from halka_arz_advisor.gemini.prompt import PROMPT_VERSION  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    aggregate_company_facts,
    infer_company_name_and_ticker,
    process_disclosure_documents,
)
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.ocr import DEFAULT_OCR_CACHE_DIR, OcrCache  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.notify.analysis_delivery import deliver_pending_analyses  # noqa: E402
from halka_arz_advisor.notify.analysis_state import load_state, save_state  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.notify.telegram import (  # noqa: E402
    TelegramConfigError,
    TelegramCredentials,
    load_credentials_from_env,
    send_message,
)
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402

DEFAULT_ANALYSIS_CACHE_DIR = Path("data") / "cache" / "llm_analysis"
DEFAULT_STATE_FILE = Path("data") / "state" / "sent_analyses.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--days", type=int, default=30, help="How many days back to look for KAP disclosures (default: 30)"
    )
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Year to fetch completed SPK IPO records for, to match against (default: current year)",
    )
    parser.add_argument("--ticker", type=str, default=None, help="Only consider this ticker (case-insensitive)")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--analysis-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_ANALYSIS_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--state-file", type=Path, default=PROJECT_ROOT / DEFAULT_STATE_FILE)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print messages that would be sent instead of sending them; never touches the state file",
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

    telegram_credentials: TelegramCredentials | None = None
    if not args.dry_run:
        try:
            telegram_credentials = load_credentials_from_env()
        except TelegramConfigError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1

    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL

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
    print(f"{len(matched)} matched target disclosure(s) to inspect", file=sys.stderr)

    # cache_only=True: same as analyze_pending_ipos.py — attachment
    # metadata is resolved live, but the PDF itself is only ever read
    # from pdf_cache, never (re-)downloaded here.
    pdf_cache = PdfCache(args.pdf_cache_dir)
    ocr_cache = OcrCache(args.ocr_cache_dir)
    processed = [
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True) for d in matched
    ]

    company_facts = aggregate_company_facts(processed)
    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in processed:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    # Computed the exact same way scripts/analyze_pending_ipos.py already
    # did, from the same cached data — required for the Gemini analysis
    # cache key (see halka_arz_advisor.decision.pipeline's module
    # docstring) to line up between the two separate script runs.
    decision_results = compute_decision_results(processed, ipo_records=tuple(ipo_records), application_records=tuple(application_records))

    if not decision_results:
        print("No companies with cached, matched documents to consider.", file=sys.stderr)

    analysis_cache = AnalysisCache(args.analysis_cache_dir)
    state, is_first_run = load_state(args.state_file)
    if is_first_run:
        state.initialized_at_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _sender(message: str) -> None:
        if args.dry_run:
            print(message)
            return
        assert telegram_credentials is not None
        send_message(telegram_credentials, message)

    result = deliver_pending_analyses(
        company_facts=company_facts,
        disclosures_by_record=disclosures_by_record,
        decision_results=decision_results,
        pdf_cache=pdf_cache,
        analysis_cache=analysis_cache,
        model=model,
        prompt_version=PROMPT_VERSION,
        state=state,
        infer_company_name_and_ticker=infer_company_name_and_ticker,
        sender=_sender,
        ocr_cache=ocr_cache,
    )

    for record_id in result.sent_record_ids:
        verb = "Would send" if args.dry_run else "Sent"
        print(f"{verb} analysis notification for {record_id}", file=sys.stderr)
    for record_id in result.failed_record_ids:
        print(f"FAILED to send for {record_id}", file=sys.stderr)

    if not args.dry_run:
        save_state(args.state_file, state)

    print(
        f"{len(result.sent_record_ids)} sent, {len(result.skipped_unchanged_record_ids)} already sent/unchanged, "
        f"{len(result.skipped_no_analysis_record_ids)} with no deliverable analysis yet, "
        f"{len(result.failed_record_ids)} failed",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
