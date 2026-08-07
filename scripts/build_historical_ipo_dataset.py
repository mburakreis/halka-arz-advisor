#!/usr/bin/env python3
"""Build (or inspect) the leakage-free historical IPO evaluation dataset.

Usage:
    uv run python scripts/build_historical_ipo_dataset.py
    uv run python scripts/build_historical_ipo_dataset.py --ticker QUICK --ticker SARAE
    uv run python scripts/build_historical_ipo_dataset.py --hydrate
    uv run python scripts/build_historical_ipo_dataset.py --no-outcomes
    uv run python scripts/build_historical_ipo_dataset.py --inspect

For every completed IPO this project's existing pipeline can currently
match (recent + backfilled KAP disclosures, cache-only PDF/OCR reads,
matched against SPK's completed-IPO/application records — the same
fetch-and-match sequence as ``scripts/validate_decision_engine.py``),
reconstructs a point-in-time :class:`~halka_arz_advisor.historical_dataset.models.HistoricalIpoSnapshot`
(see :mod:`halka_arz_advisor.historical_dataset` for the leakage rules
this reconstruction follows) and attaches its later market outcome (see
:mod:`halka_arz_advisor.ipo_outcomes`) as a separate label.

By default, a company still missing a readable prospectus/announcement/
price-determination-report is left as-is (only the already-cached
``kap_backfill`` state is reused, via ``merge_backfilled_disclosures`` —
no network search). Pass ``--hydrate`` to actively close that gap for
this run: :func:`halka_arz_advisor.kap.backfill.search_and_backfill` is
called per company, bounded to its own IPO lifecycle window, cached
(never repeats an already-exhausted window on a later run), and stops
cleanly — preserving whatever it already recovered — on a persistent
KAP failure (e.g. HTTP 429) rather than retrying indefinitely.

Cutoff resolution follows three precedence tiers (see
:mod:`halka_arz_advisor.historical_dataset.cutoff`): (1) a pre-cutoff
``subscription_end_date`` extracted from the prospectus/announcement,
(2) failing that, an explicit restatement of the subscription date
range in an official KAP IPO-results ("Halka Arzı Sonuçları") notice,
or (3) the same, in an already-cached issuer-IR copy of the pre-offer
announcement (cache-only — this command never crawls an issuer's site
itself; see ``scripts/ingest_issuer_ir_documents.py`` for that). Tiers
2/3 are read directly from raw document text, never through the normal
``ExtractedFacts`` pipeline, so nothing else about those documents ever
becomes a decision-input feature.

Writes the whole dataset to ``data/cache/historical_dataset/<version>/dataset.jsonl``
and prints a summary to stdout. ``--inspect`` skips fetching/building
entirely and just re-summarizes whatever's already on disk. Never
touches decision weights/thresholds, Gemini, Telegram, or exit logic —
read-only reconstruction only.
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

from halka_arz_advisor.evds.cache import EvdsCache  # noqa: E402
from halka_arz_advisor.historical_dataset import (  # noqa: E402
    build_historical_snapshot,
    collect_post_offer_cutoff_evidence,
    read_dataset,
    write_dataset,
)
from halka_arz_advisor.ipo_outcomes import IpoMarketOutcomeStore, build_ipo_market_outcome  # noqa: E402
from halka_arz_advisor.issuer_ir import IssuerIrCache, collect_supplementary_disclosures  # noqa: E402
from halka_arz_advisor.kap.backfill import merge_backfilled_disclosures, search_and_backfill  # noqa: E402
from halka_arz_advisor.kap.backfill_cache import BackfillCache  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.documents import DEFAULT_CACHE_DIR, process_disclosure_documents  # noqa: E402
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.ocr import DEFAULT_OCR_CACHE_DIR, OcrCache  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.market_prices.cache import BulletinCache  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.notify.identity import application_identity, ipo_identity  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient, SpkIpoApplicationRecord  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402
from halka_arz_advisor.spk.models import SpkIpoRecord  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", action="append", dest="tickers", help="Only build/report this ticker (repeatable; default: every matched completed IPO)")
    parser.add_argument("--year", type=int, action="append", dest="years", help="SPK completed-IPO year to search (repeatable; default: current year)")
    parser.add_argument("--days", type=int, default=90, help="How many days back to look for KAP disclosures (default: 90, matching KAP's own observed request-range limit)")
    parser.add_argument("--no-outcomes", action="store_true", help="Skip attaching the market-outcome label (no market_prices network access at all)")
    parser.add_argument(
        "--hydrate", action="store_true",
        help=(
            "Before building, actively fetch still-missing official pre-offer documents "
            "(prospectus/investor sale announcement/price determination report) via "
            "kap.backfill.search_and_backfill for each company this run considers — bounded to that "
            "company's own IPO lifecycle window, and a no-op for a company already exhaustively "
            "searched by an earlier run. Off by default (uses only the cheap, network-search-free "
            "merge_backfilled_disclosures) to avoid unnecessary KAP load on a plain rebuild/inspect."
        ),
    )
    parser.add_argument("--inspect", action="store_true", help="Skip fetching/building; just re-summarize the dataset already on disk")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--backfill-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "kap_backfill")
    parser.add_argument("--issuer-ir-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "kap_issuer_ir")
    parser.add_argument("--evds-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "evds")
    parser.add_argument("--bulletin-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "bist_bulletin")
    parser.add_argument("--outcome-store-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "ipo_outcomes")
    parser.add_argument("--dataset-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "historical_dataset")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    return parser.parse_args(argv)


def _find_application_record(
    disclosures_for_company: list[KapDisclosure], application_records: tuple[SpkIpoApplicationRecord, ...]
) -> SpkIpoApplicationRecord | None:
    company_names = {d.company_name.strip().upper() for d in disclosures_for_company if d.company_name}
    for record in application_records:
        if record.company_name.strip().upper() in company_names:
            return record
    return None


def _summarize(dataset: list[dict]) -> dict:
    total = len(dataset)
    cutoff_status = Counter(row["cutoff"]["status"] for row in dataset)
    # One of the three tiers documented in
    # halka_arz_advisor.historical_dataset.cutoff's module docstring —
    # the SPK-record tier was investigated and confirmed to never
    # contribute one (no such field exists in SPK's schema).
    cutoff_source = Counter(row["cutoff"]["source"] for row in dataset if row["cutoff"]["source"] is not None)

    usable_signals = {"participate", "limited_participation", "skip"}
    signal_counts = Counter(
        (row["decision_result"]["signal"] if row["decision_result"] else "cutoff_unresolved") for row in dataset
    )
    usable = sum(c for s, c in signal_counts.items() if s in usable_signals)
    insufficient = signal_counts.get("insufficient_data", 0)
    cutoff_unresolved = signal_counts.get("cutoff_unresolved", 0)

    outcome_coverage = {
        horizon: sum(1 for row in dataset if row["outcome"] and row["outcome"].get(field_name) is not None)
        for horizon, field_name in (
            ("1d", "first_day_return"),
            ("5d", "return_5d"),
            ("20d", "return_20d"),
            ("3m", "return_3m"),
        )
    }

    hard_rule_reasons: Counter[str] = Counter()
    for row in dataset:
        result = row["decision_result"]
        if result is None:
            continue
        for rule in result["hard_rules"]:
            if rule["triggered"]:
                hard_rule_reasons[f"{rule['rule_id']}: {rule['reason']}"] += 1

    return {
        "total_snapshots": total,
        "cutoff_status_counts": dict(cutoff_status),
        "cutoff_source_counts": dict(cutoff_source),
        "signal_counts": dict(signal_counts),
        "decisions_usable": usable,
        "decisions_insufficient_data": insufficient,
        "decisions_cutoff_unresolved": cutoff_unresolved,
        "outcome_coverage": outcome_coverage,
        "top_missing_data_causes": hard_rule_reasons.most_common(10),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.inspect:
        dataset = read_dataset(args.dataset_dir)
        print(json.dumps(_summarize(dataset), indent=2, ensure_ascii=False))
        return 0

    load_dotenv_if_present(args.env_file)
    config = ProbeConfig()
    wanted_tickers = {t.strip().upper() for t in args.tickers} if args.tickers else None
    years = args.years or [datetime.now(UTC).year]

    print(f"Fetching KAP disclosures from the last {args.days} day(s)...", file=sys.stderr)
    try:
        with KapClient(config) as kap_client:
            disclosures = kap_client.fetch_recent_disclosures(days=args.days)
    except KapApiError as exc:
        print(f"FAILED to fetch KAP disclosures: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    targets = set(target_document_types())
    target_disclosures = [d for d in disclosures if d.document_type in targets]

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

    matched = [match_disclosure(d, ipo_records=ipo_records, application_records=application_records) for d in target_disclosures]
    matched = [d for d in matched if d.match_method != "unmatched"]

    pdf_cache = PdfCache(args.pdf_cache_dir)
    ocr_cache = OcrCache(args.ocr_cache_dir)
    processed = [
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True, ocr_scanned=True, ocr_cache=ocr_cache)
        for d in matched
    ]

    disclosures_by_record: dict[str, list[KapDisclosure]] = {}
    for d in processed:
        if d.matched_spk_record_id:
            disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    ipo_by_identity = {ipo_identity(r): r for r in ipo_records}
    application_by_identity = {application_identity(r): r for r in application_records}
    backfill_cache = BackfillCache(args.backfill_cache_dir)
    # Issuer-IR supplementary documents are deliberately never merged in
    # here (neither branch below) — see
    # halka_arz_advisor.historical_dataset's module docstring for why.

    if args.hydrate:
        # Scoped to completed IPOs only (ipo_by_identity), not every
        # pending SPK application — this dataset only ever builds a
        # snapshot for a matched completed IPO (see the main loop
        # below), so hydrating an application-only record_id would be
        # pure wasted KAP load for this command's own purposes (that's
        # scripts/backfill_kap_history.py's broader job instead).
        record_ids = set(ipo_by_identity)
        if wanted_tickers is not None:
            record_ids = {
                rid for rid in record_ids
                if (ipo_by_identity[rid].borsa_kodu or "").strip().upper() in wanted_tickers
            }
        print(f"Hydrating {len(record_ids)} compan(y/ies) still missing a readable official document...", file=sys.stderr)
        try:
            with KapClient(config) as kap_client:
                for record_id in sorted(record_ids):
                    current = disclosures_by_record.get(record_id, [])
                    application_record = _find_application_record(current, tuple(application_records))
                    outcome = search_and_backfill(
                        record_id,
                        ipo_record=ipo_by_identity.get(record_id),
                        application_record=application_record,
                        current_disclosures=current,
                        ipo_records=ipo_records,
                        application_records=application_records,
                        cache=backfill_cache,
                        kap_client=kap_client,
                        pdf_cache=pdf_cache,
                        config=config,
                        ocr_scanned=True,
                        ocr_cache=ocr_cache,
                        reference_date=datetime.now(UTC).date(),
                    )
                    if outcome.disclosures:
                        # Reprocessed-and-fresh disclosures from this
                        # call, keyed to dedupe against what was already
                        # in `current` (reprocess_backfilled_disclosures
                        # re-reads everything backfill already knows).
                        by_id = {d.disclosure_id: d for d in current}
                        by_id.update({d.disclosure_id: d for d in outcome.disclosures})
                        disclosures_by_record[record_id] = list(by_id.values())
                    if outcome.searched:
                        print(
                            f"  {record_id}: searched {outcome.window[0]}..{outcome.window[1]}, "
                            f"recovered {outcome.recovered_document_types or '(nothing)'}",
                            file=sys.stderr,
                        )
        except KapApiError as exc:
            # A persistent failure (e.g. HTTP 429 after this project's
            # own bounded retry/backoff already ran) most likely means
            # every subsequent request would fail the same way — stop
            # hydrating rather than hammer the endpoint further. Every
            # company hydrated before this one already had its
            # BackfillEntry persisted individually (search_and_backfill
            # saves per-company), so build proceeds below with whatever
            # was actually recovered this run, not nothing.
            print(f"Hydration stopped early ({type(exc).__name__}: {exc}) — prior progress preserved, continuing with what's already cached", file=sys.stderr)
    else:
        # Cheap, network-search-free: only re-attaches whatever an
        # earlier scripts/backfill_kap_history.py (or an earlier
        # --hydrate run of this same script) already found and cached.
        merged = merge_backfilled_disclosures(
            processed,
            ipo_records=ipo_records,
            application_records=application_records,
            backfill_cache=backfill_cache,
            pdf_cache=pdf_cache,
            config=config,
            ocr_scanned=True,
            ocr_cache=ocr_cache,
        )
        disclosures_by_record = {}
        for d in merged:
            if d.matched_spk_record_id:
                disclosures_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    evds_cache = EvdsCache(args.evds_cache_dir)
    bist100_observations = evds_cache.get_observations("bist100_index")
    bulletin_cache = BulletinCache(args.bulletin_cache_dir)
    outcome_store = IpoMarketOutcomeStore(args.outcome_store_dir)

    # Cheap, crawl-free (see halka_arz_advisor.issuer_ir.ingest's own
    # convention): only re-attaches whatever an earlier
    # scripts/ingest_issuer_ir_documents.py run already found and
    # cached, for tier 3 of cutoff resolution
    # (halka_arz_advisor.historical_dataset.cutoff) — never used as
    # feature evidence (see build_historical_snapshot's own docstring).
    issuer_ir_disclosures = collect_supplementary_disclosures(
        ipo_records=ipo_records,
        application_records=application_records,
        cache=IssuerIrCache(args.issuer_ir_cache_dir),
        pdf_cache=pdf_cache,
        config=config,
        ocr_scanned=True,
        ocr_cache=ocr_cache,
    )
    issuer_ir_by_record: dict[str, list[KapDisclosure]] = {}
    for d in issuer_ir_disclosures:
        if d.matched_spk_record_id:
            issuer_ir_by_record.setdefault(d.matched_spk_record_id, []).append(d)

    generated_at = datetime.now(UTC)
    reference_date = generated_at.date()
    snapshots = []
    for record_id in sorted(disclosures_by_record):
        spk_record = ipo_by_identity.get(record_id)
        if spk_record is None:
            # A pre-completion application, or a company otherwise
            # unmatched to a completed IPO — this dataset is scoped to
            # completed IPOs (they're the only ones with a real market
            # outcome to evaluate against), so skip.
            continue
        ticker = spk_record.borsa_kodu
        if wanted_tickers is not None and (ticker or "").strip().upper() not in wanted_tickers:
            continue

        disclosures_for_company = disclosures_by_record[record_id]
        application_record = _find_application_record(disclosures_for_company, tuple(application_records))

        # Tier 2/3 cutoff evidence — read directly from official
        # post-offer documents (never through the normal ExtractedFacts
        # pipeline, so nothing here can become a scored feature; see
        # halka_arz_advisor.historical_dataset.post_offer_evidence).
        ipo_results_for_company = [d for d in disclosures_for_company if d.document_type == "ipo_results"]
        post_offer_evidence = collect_post_offer_cutoff_evidence(
            ipo_results_for_company,
            issuer_ir_by_record.get(record_id, []),
            config=config,
            pdf_cache=pdf_cache,
            ocr_cache=ocr_cache,
        )

        outcome = None
        if not args.no_outcomes and ticker:
            outcome = build_ipo_market_outcome(
                ticker,
                ipo_record=spk_record,
                disclosures=[d for d in disclosures_for_company if d.document_type == "trading_start"],
                company_name=spk_record.sirket_unvani,
                bulletin_cache=bulletin_cache,
                bist100_observations=bist100_observations,
                reference_date=reference_date,
                probe_config=config,
            )
            outcome_store.put(outcome)

        snapshot = build_historical_snapshot(
            record_id,
            ticker=ticker,
            spk_record=spk_record,
            application_record=application_record,
            disclosures=disclosures_for_company,
            evds_cache=evds_cache,
            post_offer_cutoff_evidence=post_offer_evidence,
            outcome=outcome,
            generated_at=generated_at,
        )
        snapshots.append(snapshot)
        signal = snapshot.decision_result.signal if snapshot.decision_result else f"cutoff_{snapshot.cutoff.status}"
        print(
            f"  {ticker or record_id}: cutoff={snapshot.cutoff.cutoff_date} (source={snapshot.cutoff.source}), signal={signal}",
            file=sys.stderr,
        )

    written_path = write_dataset(snapshots, args.dataset_dir)
    print(f"Wrote {len(snapshots)} snapshot(s) to {written_path}", file=sys.stderr)

    print(json.dumps(_summarize(read_dataset(args.dataset_dir)), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
