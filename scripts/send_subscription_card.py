#!/usr/bin/env python3
"""Build (and optionally send) one company's SubscriptionDecisionV1
Telegram card from cached KAP/SPK/EVDS data.

Usage:
    uv run python scripts/send_subscription_card.py --ticker EKDMR
    uv run python scripts/send_subscription_card.py --ticker EKDMR --send

Cache-only by default (never downloads a document, never runs OCR,
never calls EVDS/SPK/KAP beyond the same small live calls
scripts/validate_decision_engine.py already makes for matching). Manual
field confirmations are read from (and, with ``--confirm``, written to)
halka_arz_advisor.kap.manual_confirmation.ManualConfirmationStore, so a
human-supplied value is reusable on every later run of this script.

This is deliberately a standalone operator tool, not wired into the
scheduled expert_v0/Gemini/Telegram pipeline (scripts/check_and_notify.py) —
see halka_arz_advisor.decision.subscription_v1's own module docstring
for why this decision is kept fully separate from that one.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.decision.pipeline import resolve_company_identity  # noqa: E402
from halka_arz_advisor.decision.subscription_v1 import SubscriptionDecisionInputs, evaluate_subscription_decision  # noqa: E402
from halka_arz_advisor.evds.cache import EvdsCache  # noqa: E402
from halka_arz_advisor.evds.features import build_market_context_snapshot  # noqa: E402
from halka_arz_advisor.kap.backfill import merge_backfilled_disclosures  # noqa: E402
from halka_arz_advisor.kap.backfill_cache import BackfillCache  # noqa: E402
from halka_arz_advisor.kap.classification import target_document_types  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.derived_financials import compute_derived_financial_features  # noqa: E402
from halka_arz_advisor.kap.documents import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    aggregate_company_facts,
    aggregate_company_financial_series,
    process_disclosure_documents,
)
from halka_arz_advisor.ipo_outcomes import IpoMarketOutcomeStore, load_all_outcomes  # noqa: E402
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.kap.manual_confirmation import (  # noqa: E402
    DEFAULT_MANUAL_CONFIRMATION_CACHE_DIR,
    ManualConfirmationStore,
    ManualFieldConfirmation,
    complete_offering_terms,
)
from halka_arz_advisor.kap.matching import match_disclosure  # noqa: E402
from halka_arz_advisor.kap.models import KapDisclosure  # noqa: E402
from halka_arz_advisor.kap.ocr import DEFAULT_OCR_CACHE_DIR, OcrCache  # noqa: E402
from halka_arz_advisor.kap.offering_terms import build_offering_terms  # noqa: E402
from halka_arz_advisor.kap.pdf import PdfCache  # noqa: E402
from halka_arz_advisor.kap.sector import classify_sector  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.notify.subscription_card import format_subscription_card  # noqa: E402
from halka_arz_advisor.notify.telegram import load_credentials_from_env, send_message  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.application_list import SpkApplicationListClient  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402

_PRE_OFFER_TYPES = {"approved_prospectus", "investor_sale_announcement"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True, help="The company's ticker (case-insensitive)")
    parser.add_argument("--days", type=int, default=90, help="How many days back to look for recent KAP disclosures")
    parser.add_argument("--year", type=int, default=datetime.now(UTC).year, help="SPK completed-IPO year to match against")
    parser.add_argument("--pdf-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--ocr-cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_OCR_CACHE_DIR)
    parser.add_argument("--backfill-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "kap_backfill")
    parser.add_argument("--manual-confirmation-dir", type=Path, default=PROJECT_ROOT / DEFAULT_MANUAL_CONFIRMATION_CACHE_DIR)
    parser.add_argument("--evds-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "evds")
    parser.add_argument("--ipo-outcomes-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "ipo_outcomes")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument(
        "--confirm", action="append", default=[], metavar="FIELD=VALUE",
        help="Record a manual confirmation before building the card, e.g. --confirm offer_price=12.5 "
        "(repeatable). Persisted via ManualConfirmationStore for reuse on later runs.",
    )
    parser.add_argument("--confirmed-by", default="operator", help="Who to attribute --confirm values to")
    parser.add_argument("--send", action="store_true", help="Actually send the card via Telegram (default: print only)")
    parser.add_argument(
        "--as-of", type=lambda s: date.fromisoformat(s), default=None,
        help="Evaluate as of this date instead of now (YYYY-MM-DD) — e.g. to see what the card would have "
        "shown while a since-completed IPO's subscription window was still open",
    )
    return parser.parse_args(argv)


def _matches_ticker(disclosure: KapDisclosure, ticker: str) -> bool:
    wanted = ticker.strip().upper()
    if disclosure.ticker and disclosure.ticker.upper() == wanted:
        return True
    if disclosure.matched_spk_record_id and disclosure.matched_spk_record_id.startswith(f"ipo:{wanted}:"):
        return True
    return wanted in disclosure.company_name.upper()


def _parse_confirm_arg(raw: str, confirmed_by: str) -> ManualFieldConfirmation:
    if "=" not in raw:
        raise SystemExit(f"--confirm value must be FIELD=VALUE, got: {raw!r}")
    field_name, raw_value = raw.split("=", 1)
    field_name = field_name.strip()
    raw_value = raw_value.strip()
    if field_name in ("subscription_start", "subscription_end"):
        value: object = date.fromisoformat(raw_value)
    elif field_name in ("retail_distribution_rule", "distribution_method"):
        value = raw_value
    else:
        value = float(raw_value)
    return ManualFieldConfirmation(field_name=field_name, value=value, confirmed_by=confirmed_by, confirmed_at=datetime.now(UTC))


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
    target_disclosures = [d for d in disclosures if d.document_type in targets and _matches_ticker(d, args.ticker)]

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
        process_disclosure_documents(d, config=config, cache=pdf_cache, cache_only=True, ocr_scanned=True, ocr_cache=ocr_cache)
        for d in matched
    ]
    processed = merge_backfilled_disclosures(
        processed, ipo_records=ipo_records, application_records=application_records,
        backfill_cache=BackfillCache(args.backfill_cache_dir), pdf_cache=pdf_cache, config=config,
        ocr_scanned=True, ocr_cache=ocr_cache,
    )

    if not processed:
        print(f"No cached, matched disclosures found for ticker {args.ticker!r}.", file=sys.stderr)
        return 1

    record_id = next((d.matched_spk_record_id for d in processed if d.matched_spk_record_id), None)
    if record_id is None:
        print(f"No matched SPK record for ticker {args.ticker!r}.", file=sys.stderr)
        return 1

    company_name, ticker = resolve_company_identity(record_id, processed, ipo_records=ipo_records, application_records=application_records)

    manual_store = ManualConfirmationStore(args.manual_confirmation_dir)
    for raw_confirm in args.confirm:
        confirmation = _parse_confirm_arg(raw_confirm, args.confirmed_by)
        manual_store.add_confirmation(record_id, confirmation)
        print(f"Recorded manual confirmation: {confirmation.field_name} = {confirmation.value}", file=sys.stderr)
    confirmations = manual_store.get(record_id)

    company_facts = aggregate_company_facts(processed).get(record_id)
    pre_offer_disclosures = [d for d in processed if d.matched_spk_record_id == record_id and d.document_type in _PRE_OFFER_TYPES]
    offering_terms = build_offering_terms(company_facts, pre_offer_disclosures)
    completed_terms = complete_offering_terms(offering_terms, confirmations)

    financial_series = aggregate_company_financial_series(processed).get(record_id, ())
    sector = classify_sector(company_name)
    derived_financials = compute_derived_financial_features(financial_series, company_facts, sector=sector)

    market_context = None
    evds_cache = EvdsCache(args.evds_cache_dir)
    bist100 = evds_cache.get_observations("bist100_index")
    if bist100:
        market_context = build_market_context_snapshot(
            bist100_index=bist100,
            policy_rate_observations=evds_cache.get_observations("policy_rate"),
            tlref_observations=evds_cache.get_observations("tlref_rate"),
            cpi_observations=evds_cache.get_observations("cpi_index"),
        )

    outcome_store = IpoMarketOutcomeStore(args.ipo_outcomes_dir)
    recent_ipo_outcomes = load_all_outcomes(outcome_store)

    company_disclosures = [d for d in processed if d.matched_spk_record_id == record_id]
    as_of = datetime.combine(args.as_of, datetime.min.time()) if args.as_of else datetime.now(UTC)
    inputs = SubscriptionDecisionInputs(
        offering_terms=offering_terms, completed_terms=completed_terms, derived_financials=derived_financials,
        market_context=market_context, as_of=as_of, ticker=ticker, recent_ipo_outcomes=recent_ipo_outcomes,
        disclosures=tuple(company_disclosures),
    )
    decision = evaluate_subscription_decision(inputs)

    disclosure_urls = {d.disclosure_id: d.notification_url for d in company_disclosures if d.notification_url}
    message = format_subscription_card(
        company_name=company_name, ticker=ticker, offering_terms=offering_terms, completed_terms=completed_terms,
        decision=decision, market_context=market_context, disclosure_notification_urls=disclosure_urls,
    )

    print(message)
    print(
        f"\n[action={decision.action} edge={decision.subscription_edge} mechanics={decision.mechanics_state} "
        f"financial_quality={decision.financial_quality} ownership={decision.ownership_view} "
        f"sub_evidence={decision.subscription_evidence_grade} own_evidence={decision.ownership_evidence_grade} "
        f"regime={decision.recent_ipo_regime.status}({decision.recent_ipo_regime.mature_ipo_count})]",
        file=sys.stderr,
    )

    if args.send:
        credentials = load_credentials_from_env()
        send_message(credentials, message, config=config)
        print("Sent via Telegram.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
