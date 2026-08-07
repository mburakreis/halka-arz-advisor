#!/usr/bin/env python3
"""Refresh the local EVDS (TCMB) market-context cache.

Usage:
    uv run python scripts/refresh_evds_market_context.py
    uv run python scripts/refresh_evds_market_context.py --snapshot-only

Fetches whatever's missing since each pinned series' own last-cached
date (see halka_arz_advisor.evds.registry) — batched into one request
per frequency (daily: BIST 100 level/volume, TCMB policy rate, BIST
TLREF; monthly: TÜİK headline CPI) — and merges the result into
data/cache/evds/. Requires EVDS_API_KEY; if it's unset, this prints a
notice and exits 0 rather than failing — cached market-context data
(and anything reading it, e.g. scripts/validate_decision_engine.py)
remains usable either way.

This command only refreshes the cache and reports the resulting
snapshot; it never computes or prints decision results. No scoring,
weight, or threshold changes happen anywhere in this project as a
result of running it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.evds import (  # noqa: E402
    EvdsCache,
    build_market_context_snapshot,
    load_evds_config_from_env,
    refresh_market_context,
)
from halka_arz_advisor.notify.env import load_dotenv_if_present  # noqa: E402

DEFAULT_CACHE_DIR = Path("data") / "cache" / "evds"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", type=Path, default=PROJECT_ROOT / DEFAULT_CACHE_DIR)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument(
        "--snapshot-only", action="store_true",
        help="Skip the network refresh and just print the market-context snapshot computed from whatever's already cached",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv_if_present(args.env_file)

    cache = EvdsCache(args.cache_dir)

    if not args.snapshot_only:
        config = load_evds_config_from_env()
        if config is None:
            print("EVDS_API_KEY is not set — skipping the EVDS refresh; cached market-context data (if any) remains usable.", file=sys.stderr)
        else:
            outcome = refresh_market_context(cache, config=config, reference_date=datetime.now(UTC).date())
            print(f"Refreshed: {outcome.refreshed_series_keys}", file=sys.stderr)
            if outcome.failed_series_keys:
                print(f"Failed: {outcome.failed_series_keys}", file=sys.stderr)

    snapshot = build_market_context_snapshot(
        bist100_index=cache.get_observations("bist100_index"),
        policy_rate_observations=cache.get_observations("policy_rate"),
        tlref_observations=cache.get_observations("tlref_rate"),
        cpi_observations=cache.get_observations("cpi_index"),
    )
    payload = {
        name: {"value": fv.value, "as_of_date": fv.as_of_date.isoformat(), "source_series_codes": list(fv.source_series_codes)}
        for name, fv in sorted(snapshot.features.items())
    }
    print(json.dumps({"market_context": payload}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
