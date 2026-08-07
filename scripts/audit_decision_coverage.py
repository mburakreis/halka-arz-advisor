#!/usr/bin/env python3
"""Decision-feature coverage audit for cached IPO data.

Usage:
    uv run python scripts/audit_decision_coverage.py
    uv run python scripts/audit_decision_coverage.py --ticker QUICK

Evaluates every feature in halka_arz_advisor.decision.catalog.FEATURE_CATALOG
against each matched company's currently available KAP/SPK data and
prints deterministic JSON (one entry per company, features in fixed
catalog order, sort_keys=True) to stdout, plus a concise per-category
status summary to stderr.

Reuses the same KAP/SPK fetch-and-match pipeline as
scripts/analyze_pending_ipos.py, in cache-only mode — this command
never downloads a KAP document and never runs OCR itself, it only reads
whatever scripts/fetch_kap_disclosures.py (optionally with
--ocr-scanned) already cached.

No scoring, weighting, normalization, new external sources, conflict
resolution, Gemini changes, Telegram changes, or OCR behavior changes
happen here — see halka_arz_advisor.decision's package docstring.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.decision import CATEGORIES, FEATURE_CATALOG, audit_company  # noqa: E402
from halka_arz_advisor.decision.audit import CompanyDecisionInputs  # noqa: E402
from halka_arz_advisor.evds import EvdsCache, build_market_context_snapshot  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import DEFAULT_CACHE_DIR, aggregate_company_facts, process_disclosure_documents  # noqa: E402
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.ocr import DEFAULT_OCR_CACHE_DIR, OcrCache  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient, SpkIpoApplicationRecord  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402
from halka_arz_advisor.spk.models import SpkIpoRecord  # noqa: E402

ALL_STATUSES = (
    "AVAILABLE",
    "DERIVABLE",
    "MISSING_FIELD",
    "MISSING_DOCUMENT",
    "CONFLICTED",
    "POST_OFFER_ONLY",
    "NOT_APPLICABLE",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--days", type=int, default=30, help="How many days back to look for KAP disclosures (default: 30)"
    )
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Year to fetch completed SPK IPO records for, to match against (default: current year)",
    )
    parser.add_argument("--ticker", type=str, default=None, help="Only audit this ticker (case-insensitive)")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--evds-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "evds")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    return parser.parse_args(argv)


def _matches_ticker_filter(disclosure: KapDisclosure, ticker_filter: str) -> bool:
    wanted = ticker_filter.strip().upper()
    if disclosure.ticker and disclosure.ticker.upper() == wanted:
        return True
    if disclosure.matched_spk_record_id and disclosure.matched_spk_record_id.startswith(f"ipo:{wanted}:"):
        return True
    return wanted in disclosure.company_name.upper()


def _ipo_identity(record: SpkIpoRecord) -> str:
    # Mirrors halka_arz_advisor.notify.identity.ipo_identity exactly —
    # duplicated (not imported) since that module is notification-state
    # specific and this script has no notification concerns.
    company_key = record.borsa_kodu or record.sirket_unvani or "unknown"
    return f"ipo:{company_key}:{record.donem or ''}"


def _find_spk_record(record_id: str, ipo_records: list[SpkIpoRecord]) -> SpkIpoRecord | None:
    return next((r for r in ipo_records if _ipo_identity(r) == record_id), None)


def _find_application_record(
    disclosures_for_company: list[KapDisclosure], application_records: list[SpkIpoApplicationRecord]
) -> SpkIpoApplicationRecord | None:
    company_names = {d.company_name.strip().upper() for d in disclosures_for_company if d.company_name}
    for record in application_records:
        if record.company_name.strip().upper() in company_names:
            return record
    return None


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

    matched = [
        match_disclosure(d, ipo_records=ipo_records, application_records=application_records)
        for d in target_disclosures
    ]
    matched = [d for d in matched if d.match_method != "unmatched"]
    if args.ticker:
        matched = [d for d in matched if _matches_ticker_filter(d, args.ticker)]
    print(f"{len(matched)} matched target disclosure(s) to inspect for cached documents", file=sys.stderr)

    # cache_only=True, ocr_scanned=True: reads whatever fetch_kap_disclosures.py
    # already cached (PDF text and, if --ocr-scanned was used there, OCR
    # text) — never downloads, never runs OCR from here.
    pdf_cache = PdfCache(args.pdf_cache_dir)
    ocr_cache = OcrCache(args.ocr_cache_dir)
    processed = [
        process_disclosure_documents(
            d, config=config, cache=pdf_cache, cache_only=True, ocr_scanned=True, ocr_cache=ocr_cache
        )
        for d in matched
    ]

    company_facts = aggregate_company_facts(processed)
    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in processed:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    record_ids = sorted(set(disclosures_by_record) | set(company_facts))
    if not record_ids:
        print("No companies with cached, matched documents to audit.", file=sys.stderr)
        print(json.dumps({"companies": [], "aggregate_summary": {}}, indent=2, sort_keys=True))
        return 0

    print(f"{len(record_ids)} compan(y/ies) to audit against {len(FEATURE_CATALOG)} catalog feature(s)", file=sys.stderr)

    # Cache-only, network-free — reads whatever scripts/refresh_evds_market_context.py
    # already cached (see halka_arz_advisor.evds). Company-agnostic: the
    # same snapshot is attached to every company below. No refresh is
    # attempted here even if EVDS_API_KEY is set; a missing/empty cache
    # just means every market_context.* feature below stays MISSING_DOCUMENT,
    # same as before this existed.
    evds_cache = EvdsCache(args.evds_cache_dir)
    market_context = build_market_context_snapshot(
        bist100_index=evds_cache.get_observations("bist100_index"),
        policy_rate_observations=evds_cache.get_observations("policy_rate"),
        tlref_observations=evds_cache.get_observations("tlref_rate"),
        cpi_observations=evds_cache.get_observations("cpi_index"),
    )

    aggregate_summary: dict[str, int] = {status: 0 for status in ALL_STATUSES}
    companies_output = []

    for record_id in record_ids:
        disclosures_for_company = tuple(disclosures_by_record.get(record_id, []))
        facts = company_facts.get(record_id)
        spk_record = _find_spk_record(record_id, ipo_records)
        application_record = _find_application_record(list(disclosures_for_company), application_records)

        inputs = CompanyDecisionInputs(
            spk_record_id=record_id,
            spk_record=spk_record,
            application_record=application_record,
            facts=facts,
            disclosures=disclosures_for_company,
            market_context=market_context,
        )
        results = audit_company(inputs)

        company_summary: dict[str, int] = {status: 0 for status in ALL_STATUSES}
        for r in results:
            company_summary[r.status] += 1
            aggregate_summary[r.status] += 1

        companies_output.append(
            {
                "spk_record_id": record_id,
                "features": [r.as_dict() for r in results],
                "summary": company_summary,
            }
        )
        print(f"  {record_id}: {company_summary}", file=sys.stderr)

    print(f"Aggregate status summary across {len(record_ids)} compan(y/ies): {aggregate_summary}", file=sys.stderr)
    print("By category (feature counts, catalog-defined):", file=sys.stderr)
    for category in CATEGORIES:
        count = sum(1 for spec in FEATURE_CATALOG if spec.category == category)
        print(f"  {category}: {count} feature(s)", file=sys.stderr)

    output = {
        "feature_catalog_size": len(FEATURE_CATALOG),
        "companies": companies_output,
        "aggregate_summary": aggregate_summary,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
