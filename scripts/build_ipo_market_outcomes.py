#!/usr/bin/env python3
"""Build deterministic IPO market outcomes for selected completed IPOs.

Usage:
    uv run python scripts/build_ipo_market_outcomes.py --ticker QUICK --ticker SARAE
    uv run python scripts/build_ipo_market_outcomes.py --ticker QUICK --year 2026 --days 120

For each ``--ticker``: fetches that year's SPK completed-IPO record
(for ``borsada_islem_gorme_tarihi``, the trading-start date) and recent
KAP ``trading_start`` disclosures (recorded for traceability only — see
halka_arz_advisor.ipo_outcomes.trading_start for why this project
verified live that this disclosure's own publish date is an advance
announcement, not the trading date, and so is not used as a competing
date), resolves the actual first trading date, pulls that ticker's
official Borsa İstanbul daily-bulletin
price history (halka_arz_advisor.market_prices, cached by calendar date
so overlapping tickers/re-runs never re-download a bulletin), and
computes first_day_return / return_5d / return_20d / return_3m /
max_drawdown_5d / max_drawdown_20d / max_drawdown_3m plus their
BIST-100-relative counterparts against whatever is already cached in
data/cache/evds (this command never fetches EVDS itself — see
scripts/refresh_evds_market_context.py for that).

Writes each result to data/cache/ipo_outcomes/<TICKER>.json and prints a
JSON summary to stdout. Never touches halka_arz_advisor.decision,
gemini, or notify — this is a read-only, backtest-oriented data layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.evds.cache import EvdsCache  # noqa: E402
from halka_arz_advisor.ipo_outcomes import IpoMarketOutcomeStore, build_ipo_market_outcome  # noqa: E402
from halka_arz_advisor.ipo_outcomes.models import outcome_to_dict  # noqa: E402
from halka_arz_advisor.kap.client import KapClient  # noqa: E402
from halka_arz_advisor.kap.exceptions import KapApiError  # noqa: E402
from halka_arz_advisor.market_prices.cache import BulletinCache  # noqa: E402
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402
from halka_arz_advisor.probe.config import ProbeConfig  # noqa: E402
from halka_arz_advisor.spk.client import SpkApiClient  # noqa: E402
from halka_arz_advisor.spk.exceptions import SpkApiError  # noqa: E402
from halka_arz_advisor.spk.models import SpkIpoRecord  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", action="append", required=True, dest="tickers", help="A completed IPO's ticker (repeatable)")
    parser.add_argument(
        "--year", type=int, action="append", dest="years",
        help="SPK completed-IPO year to search for each ticker's record (repeatable; default: current year)",
    )
    parser.add_argument("--days", type=int, default=90, help="How many days back to look for a KAP trading_start disclosure, recorded for traceability only (default: 90, matching KAP's own observed request-range limit)")
    parser.add_argument("--evds-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "evds")
    parser.add_argument("--bulletin-cache-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "bist_bulletin")
    parser.add_argument("--outcome-store-dir", type=Path, default=PROJECT_ROOT / "data" / "cache" / "ipo_outcomes")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    return parser.parse_args(argv)


def _find_ipo_record(ticker: str, ipo_records: list[SpkIpoRecord]) -> SpkIpoRecord | None:
    wanted = ticker.strip().upper()
    return next((r for r in ipo_records if r.borsa_kodu and r.borsa_kodu.strip().upper() == wanted), None)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv_if_present(args.env_file)
    config = ProbeConfig()
    tickers = [t.strip().upper() for t in args.tickers]
    years = args.years or [datetime.now(UTC).year]

    print(f"Fetching SPK completed-IPO records for year(s) {years}...", file=sys.stderr)
    ipo_records: list[SpkIpoRecord] = []
    try:
        with SpkApiClient(config) as spk_client:
            for year in years:
                ipo_records.extend(spk_client.get_initial_public_offerings(year))
    except SpkApiError as exc:
        print(f"FAILED to fetch SPK IPO records: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Fetching KAP disclosures from the last {args.days} day(s) (trading_start dates recorded for traceability only)...", file=sys.stderr)
    try:
        with KapClient(config) as kap_client:
            disclosures = kap_client.fetch_recent_disclosures(days=args.days)
    except KapApiError as exc:
        print(f"FAILED to fetch KAP disclosures: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    trading_start_disclosures = [d for d in disclosures if d.document_type == "trading_start"]

    evds_cache = EvdsCache(args.evds_cache_dir)
    bist100_observations = evds_cache.get_observations("bist100_index")
    print(f"Using {len(bist100_observations)} already-cached BIST 100 observation(s) as benchmark; no new EVDS fetch.", file=sys.stderr)

    bulletin_cache = BulletinCache(args.bulletin_cache_dir)
    store = IpoMarketOutcomeStore(args.outcome_store_dir)

    reference_date = datetime.now(UTC).date()
    outcomes = []
    for ticker in tickers:
        ipo_record = _find_ipo_record(ticker, ipo_records)
        if ipo_record is None:
            print(f"WARNING: no SPK completed-IPO record found for ticker {ticker!r} in year(s) {years}", file=sys.stderr)
        ticker_disclosures = [d for d in trading_start_disclosures if d.ticker and d.ticker.strip().upper() == ticker]

        outcome = build_ipo_market_outcome(
            ticker,
            ipo_record=ipo_record,
            disclosures=ticker_disclosures,
            company_name=ipo_record.sirket_unvani if ipo_record else None,
            bulletin_cache=bulletin_cache,
            bist100_observations=bist100_observations,
            reference_date=reference_date,
            probe_config=config,
        )
        store.put(outcome)
        outcomes.append(outcome)
        if outcome.resolved_trading_start_date is None:
            print(f"WARNING: {ticker} has no known SPK trading-start date; no outcome computed.", file=sys.stderr)

    print(json.dumps({"outcomes": [outcome_to_dict(o) for o in outcomes]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
