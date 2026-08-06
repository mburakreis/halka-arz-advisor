#!/usr/bin/env python3
"""Notification MVP: check SPK for new completed IPOs and new IPO
applications, and notify about anything not seen in a previous run.

Usage:
    uv run python scripts/check_and_notify.py
    uv run python scripts/check_and_notify.py --dry-run
    uv run python scripts/check_and_notify.py --send-existing   # meaningful on the first run only
    uv run python scripts/check_and_notify.py --year 2025

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (see .env.example, or
export them directly) unless --dry-run is passed. Seen-record identities
persist in data/state/seen_records.json between runs.

Out of scope here: scoring, recommendations, KAP, a database, ML, a UI,
and scheduling (run this by hand or from cron/launchd yourself for now).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from halka_arz_advisor.notify.check import check_and_notify  # noqa: E402
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--year", type=int, default=datetime.now(UTC).year,
        help="Year to check for completed IPOs (default: current year)",
    )
    parser.add_argument(
        "--state-file", type=Path, default=PROJECT_ROOT / "data" / "state" / "seen_records.json",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print notifications instead of sending them via Telegram",
    )
    parser.add_argument(
        "--send-existing", action="store_true",
        help="On the first run (no state file yet), also notify about everything already found "
        "instead of just recording it silently",
    )
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    return parser.parse_args(argv)


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

    def notifier(text: str) -> None:
        if args.dry_run:
            print("----- DRY RUN: would send -----")
            print(text)
            print("--------------------------------")
        else:
            assert telegram_credentials is not None
            send_message(telegram_credentials, text)

    config = ProbeConfig()
    with SpkApiClient(config) as ipo_client, SpkApplicationListClient(config) as application_client:
        result = check_and_notify(
            ipo_client=ipo_client,
            application_client=application_client,
            state_path=args.state_file,
            year=args.year,
            send_existing=args.send_existing,
            notifier=notifier,
        )

    notified_count = len(result.notified_ipo_records) + len(result.notified_application_records)
    print(
        f"Checked year {args.year}: {len(result.new_ipo_records)} new IPO(s), "
        f"{len(result.new_application_records)} new application(s)"
        + (" (first run)" if result.is_first_run else "")
    )
    print(f"{notified_count} notification(s) {'printed' if args.dry_run else 'sent'}")
    print(
        f"State now tracks {result.total_ipo_seen} IPO(s) and "
        f"{result.total_application_seen} application(s): {args.state_file}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
