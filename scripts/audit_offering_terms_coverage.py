#!/usr/bin/env python3
"""OfferingTerms coverage/conflict audit — read-only, cache-only.

Usage:
    uv run python scripts/audit_offering_terms_coverage.py
    uv run python scripts/audit_offering_terms_coverage.py --out /tmp/offering_terms_report.json

Same harness as ``scripts/audit_capability_report.py`` (reprocesses
every already-backfilled company's cached KAP disclosures, cache-only —
no PDF download, no fresh OCR beyond what's already cached; the only
live call is KAP's small per-disclosure attachment-metadata JSON call),
but instead of running ``decision.audit.audit_company`` against the
full 66-feature catalog, this builds each company's
:class:`~halka_arz_advisor.kap.offering_terms.OfferingTerms` (via
:func:`~halka_arz_advisor.kap.offering_terms.build_offering_terms`) from
its merged, pre-offer-safe :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`
and reports per-field extracted/conflicting/not_found counts across the
cohort.

No scoring, weighting, Gemini, or Telegram code is touched or imported.
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

from halka_arz_advisor.kap.allocation_ocr import recover_allocation_sections  # noqa: E402
from halka_arz_advisor.kap.backfill import merge_backfilled_disclosures  # noqa: E402
from halka_arz_advisor.kap.backfill_cache import BackfillCache  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import DEFAULT_CACHE_DIR, aggregate_company_facts, process_disclosure_documents  # noqa: E402
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.ocr import DEFAULT_OCR_CACHE_DIR, OcrCache  # noqa: E402
from halka_arz_advisor.kap.offering_terms import OFFERING_TERM_FIELD_NAMES, build_offering_terms, offering_terms_as_dict  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, action="append", dest="years", help="SPK completed-IPO year(s) to fetch (repeatable; default: 2024 2025 2026)")
    parser.add_argument("--days", type=int, default=90, help="How many days back to look for recent KAP disclosures (default: 90)")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--backfill-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "kap_backfill")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON report here instead of stdout")
    parser.add_argument(
        "--deep-ocr-allocation",
        action="store_true",
        help=(
            "For any company still missing investor_group_allocations/retail_allocation_percentage/"
            "retail_offered_shares, run halka_arz_advisor.kap.allocation_ocr's scoped deep-OCR fallback "
            "(cache-only PDF bytes, no new KAP crawl) before reporting coverage. Off by default since it can "
            "run real local Tesseract OCR for several minutes across a cohort with many scanned prospectuses."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv_if_present(args.env_file)
    config = ProbeConfig()
    years = args.years or [2024, 2025, 2026]

    print(f"Fetching SPK completed-IPO records for year(s) {years} and application records...", file=sys.stderr)
    try:
        ipo_records = []
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

    by_id: dict[str, KapDisclosure] = {}
    for d in merged:
        by_id[d.disclosure_id] = d
    merged = list(by_id.values())

    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in merged:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    # Only approved_prospectus/investor_sale_announcement disclosures are
    # ever fed to build_offering_terms — the same pre-offer-safe document
    # scope kap_extraction itself is limited to for these fields (see
    # kap.offering_terms's module docstring). ipo_results/
    # price_determination_report disclosures are excluded here even
    # though they're present in disclosures_by_record, so a company's
    # observation provenance can never accidentally point at a post-offer
    # document for one of these fields.
    pre_offer_types = {"approved_prospectus", "investor_sale_announcement"}
    company_facts = aggregate_company_facts(merged)

    record_ids = sorted(rid for rid in disclosures_by_record if rid in company_facts)
    print(f"{len(record_ids)} completed IPO(s) with cached, matched, pre-offer-eligible documents to audit", file=sys.stderr)

    per_field_status: dict[str, Counter] = {name: Counter() for name in OFFERING_TERM_FIELD_NAMES}
    per_field_derived: dict[str, Counter] = {name: Counter() for name in OFFERING_TERM_FIELD_NAMES}
    per_company_output = []

    for record_id in record_ids:
        disclosures = [d for d in disclosures_by_record[record_id] if d.document_type in pre_offer_types]
        facts = company_facts.get(record_id)
        terms = build_offering_terms(facts, disclosures)

        if args.deep_ocr_allocation:
            recovery = recover_allocation_sections(
                record_id, disclosures_by_record[record_id], pdf_cache=pdf_cache, ocr_cache=ocr_cache,
            )
            if not recovery.already_resolved:
                print(
                    f"  {record_id}: deep-OCR allocation recovery — {len(recovery.attempts)} step(s), "
                    f"resolved={recovery.resolved}",
                    file=sys.stderr,
                )
            terms = recovery.offering_terms

        for name in OFFERING_TERM_FIELD_NAMES:
            field = getattr(terms, name)
            per_field_status[name][field.status] += 1
            if field.status == "extracted":
                per_field_derived[name]["derived" if field.derived else "direct"] += 1
        per_company_output.append({"spk_record_id": record_id, "offering_terms": offering_terms_as_dict(terms)})
        counts = {name: getattr(terms, name).status for name in OFFERING_TERM_FIELD_NAMES}
        print(f"  {record_id}: {counts}", file=sys.stderr)

    total = len(record_ids)
    field_summaries = []
    for name in OFFERING_TERM_FIELD_NAMES:
        status_counts = per_field_status[name]
        extracted = status_counts.get("extracted", 0)
        field_summaries.append(
            {
                "field": name,
                "total_companies": total,
                "extracted_count": extracted,
                "extracted_pct": round(100 * extracted / total, 1) if total else None,
                "conflicting_count": status_counts.get("conflicting", 0),
                "not_found_count": status_counts.get("not_found", 0),
                "direct_vs_derived": dict(per_field_derived[name]),
            }
        )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_companies_audited": total,
        "field_summaries": field_summaries,
        "per_company": per_company_output,
    }

    text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False, default=str)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote report to {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
