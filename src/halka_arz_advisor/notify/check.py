"""Orchestration: poll SPK, diff against persisted state, notify about new records.

Kept deliberately thin — no scoring, no matching applications to
completed IPOs, no scheduling. One pass: fetch, diff, notify, persist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..spk.application_list import SpkApplicationListClient, SpkIpoApplicationRecord
from ..spk.client import SpkApiClient
from ..spk.models import SpkIpoRecord
from .formatting import format_application_notification, format_ipo_notification
from .identity import application_identity, ipo_identity
from .state import load_state, save_state

Notifier = Callable[[str], None]


@dataclass(slots=True)
class CheckResult:
    is_first_run: bool
    new_ipo_records: list[SpkIpoRecord]
    new_application_records: list[SpkIpoApplicationRecord]
    notified_ipo_records: list[SpkIpoRecord]
    notified_application_records: list[SpkIpoApplicationRecord]
    total_ipo_seen: int
    total_application_seen: int


def check_and_notify(
    *,
    ipo_client: SpkApiClient,
    application_client: SpkApplicationListClient,
    state_path: Path,
    year: int,
    send_existing: bool,
    notifier: Notifier,
) -> CheckResult:
    """Fetch both sources, notify about anything not already in ``state_path``.

    On the very first run (no state file yet), nothing is sent unless
    ``send_existing`` is True — but everything fetched this run is still
    recorded, so only genuinely new records trigger a notification from
    the next run onward.
    """
    state, is_first_run = load_state(state_path)

    ipo_records = ipo_client.get_initial_public_offerings(year)
    application_records = application_client.get_applications()

    new_ipo_records = [r for r in ipo_records if ipo_identity(r) not in state.ipo_identities]
    new_application_records = [
        r for r in application_records if application_identity(r) not in state.application_identities
    ]

    should_notify = send_existing or not is_first_run
    notified_ipo_records = new_ipo_records if should_notify else []
    notified_application_records = new_application_records if should_notify else []

    for record in notified_ipo_records:
        notifier(format_ipo_notification(record))
    for record in notified_application_records:
        notifier(format_application_notification(record))

    state.ipo_identities |= {ipo_identity(r) for r in ipo_records}
    state.application_identities |= {application_identity(r) for r in application_records}
    if is_first_run:
        state.initialized_at_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(state_path, state)

    return CheckResult(
        is_first_run=is_first_run,
        new_ipo_records=new_ipo_records,
        new_application_records=new_application_records,
        notified_ipo_records=notified_ipo_records,
        notified_application_records=notified_application_records,
        total_ipo_seen=len(state.ipo_identities),
        total_application_seen=len(state.application_identities),
    )
