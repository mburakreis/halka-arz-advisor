#!/usr/bin/env python3
"""KAP-independent official-document fallback: recover missing IPO
lifecycle documents from a registered issuer's own investor-relations
page.

Usage:
    uv run python scripts/ingest_issuer_ir_documents.py
    uv run python scripts/ingest_issuer_ir_documents.py --ticker QUICK

For every ticker in halka_arz_advisor.issuer_ir's registry (currently
QUICK, MASFN, METEN) with a matched SPK completed-IPO or application
record, this checks which of the five issuer_ir-supported document
types (approved_prospectus, investor_sale_announcement,
price_determination_report, financial_statement_attachment,
use_of_proceeds_report) are still missing and, only if so, fetches the
issuer's own registered IPO page and ingests same-domain PDF links whose
link text deterministically classifies to one of those types — no LLM
anywhere in discovery or classification.

Reuses the existing PDF cache, OCR fallback, and field/financial
extraction pipeline unmodified (see halka_arz_advisor.issuer_ir.ingest).
A byte-identical duplicate of a document already cached (whether from
KAP or a prior issuer_ir run) is recognized and skipped, never
double-counted as a new recovery. Results are persisted under
data/cache/kap_issuer_ir/ so a scheduled run never repeats an
already-satisfied crawl of the same page.

This command only discovers and caches documents; it does not compute
or print decision results (see scripts/validate_decision_engine.py,
which merges in whatever this cached before recomputing the
deterministic decision).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.issuer_ir import get_issuer_ir_source, registered_tickers  # noqa: E402
from halka_arz_advisor.issuer_ir.cache import IssuerIrCache  # noqa: E402
from halka_arz_advisor.issuer_ir.crawler import SUPPORTED_ISSUER_IR_DOCUMENT_TYPES  # noqa: E402
from halka_arz_advisor.issuer_ir.ingest import resolve_registered_record_id, search_and_ingest  # noqa: E402
from halka_arz_advisor.kap.backfill import missing_document_types  # noqa: E402
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

DEFAULT_ISSUER_IR_CACHE_DIR = Path("data") / "cache" / "kap_issuer_ir"

# financial_statement_attachment/use_of_proceeds_report have no KAP
# equivalent at all (see kap.classification's module docstring), so
# halka_arz_advisor.kap.backfill.missing_document_types never reports
# them — they're always worth a first search unless issuer_ir already
# has them (search_and_ingest checks its own cache for that).
_ALWAYS_CONSIDERED_TYPES = tuple(t for t in SUPPORTED_ISSUER_IR_DOCUMENT_TYPES if t not in target_document_types())


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
    parser.add_argument("--issuer-ir-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_ISSUER_IR_CACHE_DIR)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--ocr-scanned", action="store_true", help="Fall back to local OCR for a scanned/empty ingested PDF")
    return parser.parse_args(argv)


def _company_known_content_hashes(disclosures: list[KapDisclosure], pdf_cache: PdfCache) -> frozenset[str]:
    hashes: set[str] = set()
    for disclosure in disclosures:
        if disclosure.primary_document is None:
            continue
        pdf_bytes = pdf_cache.get(disclosure.primary_document.obj_id)
        if pdf_bytes is not None:
            hashes.add(hashlib.sha256(pdf_bytes).hexdigest())
    return frozenset(hashes)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv_if_present(args.env_file)
    config = ProbeConfig()

    tickers = [args.ticker.strip().upper()] if args.ticker else list(registered_tickers())
    sources = [(t, get_issuer_ir_source(t)) for t in tickers]
    sources = [(t, s) for t, s in sources if s is not None]
    if not sources:
        print("No registered issuer_ir source(s) match the given ticker filter.", file=sys.stderr)
        print(json.dumps({"companies": []}, indent=2))
        return 0

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
    processed = [
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True, ocr_scanned=args.ocr_scanned, ocr_cache=ocr_cache)
        for d in matched
    ]
    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in processed:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    issuer_ir_cache = IssuerIrCache(args.issuer_ir_cache_dir)

    results = []
    for ticker, source in sources:
        record_id = resolve_registered_record_id(source, ipo_records=ipo_records, application_records=application_records)
        if record_id is None:
            print(f"  {ticker}: no matched SPK completed-IPO/application record found; skipping", file=sys.stderr)
            continue

        current = disclosures_by_record.get(record_id, [])
        missing = list(missing_document_types(current)) + list(_ALWAYS_CONSIDERED_TYPES)
        known_hashes = _company_known_content_hashes(current, pdf_cache)

        outcome = search_and_ingest(
            record_id,
            source,
            missing,
            cache=issuer_ir_cache,
            pdf_cache=pdf_cache,
            known_content_hashes=known_hashes,
            config=config,
            ocr_scanned=args.ocr_scanned,
            ocr_cache=ocr_cache,
        )

        results.append(
            {
                "ticker": ticker,
                "record_id": record_id,
                "ipo_page_url": source.ipo_page_url,
                "crawled": outcome.crawled,
                "recovered_document_types": list(outcome.recovered_document_types),
                "duplicate_of_known_content": list(outcome.duplicate_of_known_content),
                "ingested_disclosure_count": len(outcome.disclosures),
            }
        )
        print(
            f"  {ticker}: crawled={outcome.crawled}, recovered={outcome.recovered_document_types or '(nothing)'}, "
            f"duplicates_skipped={len(outcome.duplicate_of_known_content)}",
            file=sys.stderr,
        )

    print(json.dumps({"companies": results}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
