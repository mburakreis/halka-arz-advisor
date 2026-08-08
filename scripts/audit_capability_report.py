#!/usr/bin/env python3
"""Capability-first audit of the full decision-feature catalog.

Usage:
    uv run python scripts/audit_capability_report.py
    uv run python scripts/audit_capability_report.py --out /tmp/capability_report.json

For every completed IPO this project has ever backfilled (i.e. has a
``BackfillEntry`` in ``data/cache/kap_backfill``), reprocesses its
already-cached KAP disclosures (cache-only PDF/OCR reads — no PDF
download, no OCR re-run; the only live call is KAP's small per-disclosure
attachment-metadata JSON call, the same one every other cache-only
script in this project makes) and a small, structured live SPK API call
for the completed-IPO/application record lists (years 2024-2026 by
default). Then runs the existing, unmodified
``halka_arz_advisor.decision.audit.audit_company`` for every completed
IPO — deliberately over the company's *entire* unfiltered document set
(no historical decision cutoff filtering), because this is a capability
audit ("what can the extraction/sourcing pipeline obtain at all, ever")
rather than a leakage-free point-in-time evaluation (that's what
``halka_arz_advisor.historical_dataset``/``build_historical_ipo_dataset.py``
is for).

Prints one big JSON report to stdout (or ``--out``): per-feature
aggregate availability/conflict counts, dominant unavailable-reason
buckets, and observed extraction methods, plus a per-company matrix.

Read-only: no scoring/weighting/extraction/Gemini/Telegram code is
touched or imported for its side effects; this script only calls
existing reporting functions (``decision.audit.audit_company``) and
prints their output.
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

from halka_arz_advisor.decision.audit import CompanyDecisionInputs, FeatureAuditResult, audit_company  # noqa: E402
from halka_arz_advisor.decision.catalog import FEATURE_CATALOG  # noqa: E402
from halka_arz_advisor.evds.cache import EvdsCache  # noqa: E402
from halka_arz_advisor.evds.features import build_market_context_snapshot  # noqa: E402
from halka_arz_advisor.kap.backfill import merge_backfilled_disclosures  # noqa: E402
from halka_arz_advisor.kap.backfill_cache import BackfillCache  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    aggregate_company_facts,
    aggregate_company_financial_series,
    process_disclosure_documents,
)
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.ocr import DEFAULT_OCR_CACHE_DIR, OcrCache  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.notify.identity import application_identity, ipo_identity  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient, SpkIpoApplicationRecord  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402
from halka_arz_advisor.spk.models import SpkIpoRecord  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, action="append", dest="years", help="SPK completed-IPO year(s) to fetch (repeatable; default: 2024 2025 2026)")
    parser.add_argument("--days", type=int, default=90, help="How many days back to look for recent KAP disclosures (default: 90, matching this project's other cache-only scripts)")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--backfill-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "kap_backfill")
    parser.add_argument("--evds-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "evds")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON report here instead of stdout")
    return parser.parse_args(argv)


def _find_application_record(
    disclosures_for_company: list[KapDisclosure], application_records: tuple[SpkIpoApplicationRecord, ...]
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
    years = args.years or [2024, 2025, 2026]

    print(f"Fetching SPK completed-IPO records for year(s) {years} and application records...", file=sys.stderr)
    try:
        ipo_records: list[SpkIpoRecord] = []
        with SpkApiClient(config) as spk_client:
            for year in years:
                ipo_records.extend(spk_client.get_initial_public_offerings(year))
    except SpkApiError as exc:
        print(f"FAILED to fetch SPK IPO records: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    try:
        with SpkApplicationListClient(config) as application_client:
            application_records = application_client.get_applications()
    except SpkApiError as exc:
        print(f"FAILED to fetch SPK application records: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    pdf_cache = PdfCache(args.pdf_cache_dir)
    ocr_cache = OcrCache(args.ocr_cache_dir)
    backfill_cache = BackfillCache(args.backfill_cache_dir)

    print(f"Fetching KAP disclosures from the last {args.days} day(s)...", file=sys.stderr)
    try:
        with KapClient(config) as kap_client:
            recent_disclosures = kap_client.fetch_recent_disclosures(days=args.days)
    except KapApiError as exc:
        print(f"FAILED to fetch recent KAP disclosures: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    targets = set(target_document_types())
    recent_target_disclosures = [d for d in recent_disclosures if d.document_type in targets]
    recent_matched = [
        match_disclosure(d, ipo_records=ipo_records, application_records=application_records)
        for d in recent_target_disclosures
    ]
    recent_matched = [d for d in recent_matched if d.match_method != "unmatched"]
    recent_processed = [
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True, ocr_scanned=True, ocr_cache=ocr_cache)
        for d in recent_matched
    ]
    print(f"{len(recent_processed)} matched, cache-read disclosure(s) from the recent-{args.days}-day window", file=sys.stderr)

    print("Reprocessing every backfilled company's cached documents (cache-only PDF/OCR) and merging...", file=sys.stderr)
    try:
        merged = merge_backfilled_disclosures(
            recent_processed,
            ipo_records=ipo_records,
            application_records=application_records,
            backfill_cache=backfill_cache,
            pdf_cache=pdf_cache,
            config=config,
            ocr_scanned=True,
            ocr_cache=ocr_cache,
        )
    except KapApiError as exc:
        print(f"FAILED reprocessing backfilled disclosures: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    # De-duplicate by disclosure_id (a document can appear in both the
    # recent-window fetch and an earlier backfill run) — last one wins,
    # matching how build_historical_ipo_dataset.py's own --hydrate path
    # already resolves the same overlap.
    by_id: dict[str, KapDisclosure] = {}
    for d in merged:
        by_id[d.disclosure_id] = d
    merged = list(by_id.values())

    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in merged:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    ipo_by_identity = {ipo_identity(r): r for r in ipo_records}

    evds_cache = EvdsCache(args.evds_cache_dir)
    market_context = build_market_context_snapshot(
        bist100_index=evds_cache.get_observations("bist100_index"),
        policy_rate_observations=evds_cache.get_observations("policy_rate"),
        tlref_observations=evds_cache.get_observations("tlref_rate"),
        cpi_observations=evds_cache.get_observations("cpi_index"),
    )

    company_facts = aggregate_company_facts(merged)
    company_financials = aggregate_company_financial_series(merged)

    record_ids = sorted(rid for rid in disclosures_by_record if rid in ipo_by_identity)
    print(f"{len(record_ids)} completed IPO(s) with cached documents to audit against {len(FEATURE_CATALOG)} catalog feature(s)", file=sys.stderr)

    companies_output = []
    per_feature_company_results: dict[str, list[tuple[str, FeatureAuditResult]]] = {spec.feature_id: [] for spec in FEATURE_CATALOG}

    for record_id in record_ids:
        spk_record = ipo_by_identity[record_id]
        ticker = spk_record.borsa_kodu or record_id
        disclosures_for_company = tuple(disclosures_by_record[record_id])
        application_record = _find_application_record(list(disclosures_for_company), tuple(application_records))

        inputs = CompanyDecisionInputs(
            spk_record_id=record_id,
            spk_record=spk_record,
            application_record=application_record,
            facts=company_facts.get(record_id),
            disclosures=disclosures_for_company,
            financial_observations=company_financials.get(record_id, ()),
            company_name=spk_record.sirket_unvani,
            market_context=market_context,
        )
        results = audit_company(inputs)
        for r in results:
            per_feature_company_results[r.feature_id].append((ticker, r))

        status_counts = Counter(r.status for r in results)
        companies_output.append(
            {
                "spk_record_id": record_id,
                "ticker": ticker,
                "company_name": spk_record.sirket_unvani,
                "status_counts": dict(status_counts),
                "features": [r.as_dict() for r in results],
            }
        )
        print(f"  {ticker}: {dict(status_counts)}", file=sys.stderr)

    total_companies = len(record_ids)
    feature_summaries = []
    for spec in FEATURE_CATALOG:
        entries = per_feature_company_results[spec.feature_id]
        status_counter = Counter(status for _t, r in entries for status in [r.status])
        available_count = status_counter.get("AVAILABLE", 0) + status_counter.get("DERIVABLE", 0)
        conflict_count = status_counter.get("CONFLICTED", 0)
        extraction_methods = Counter(
            e.extraction_method
            for _t, r in entries
            if r.status in ("AVAILABLE", "DERIVABLE")
            for e in r.evidence
            if e.extraction_method
        )
        unavailable_reasons = Counter(
            e.status
            for _t, r in entries
            if r.status not in ("AVAILABLE", "DERIVABLE")
            for e in r.evidence
            if r.status != "AVAILABLE"
        )
        feature_summaries.append(
            {
                "feature_id": spec.feature_id,
                "category": spec.category,
                "offer_timing": spec.offer_timing,
                "is_mandatory": spec.is_mandatory,
                "availability_kind": spec.availability_kind,
                "required_source_fields": list(spec.required_source_fields),
                "acceptable_sources": list(spec.acceptable_sources),
                "total_companies": total_companies,
                "available_count": available_count,
                "available_pct": round(100 * available_count / total_companies, 1) if total_companies else None,
                "conflict_count": conflict_count,
                "status_counts": dict(status_counter),
                "extraction_methods_observed": dict(extraction_methods),
                "dominant_unavailable_reasons": unavailable_reasons.most_common(5),
            }
        )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_companies_audited": total_companies,
        "feature_catalog_size": len(FEATURE_CATALOG),
        "companies": [c["ticker"] for c in companies_output],
        "feature_summaries": feature_summaries,
        "per_company": companies_output,
    }

    text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote report to {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
