"""Persisted "have we already notified about this?" state.

One JSON file (``data/state/seen_records.json`` by default) holding a
flat set of identity strings per record type. The set only grows: once
an identity has been seen it stays recorded, even if a later fetch (a
different ``--year``, a row that fell off the live page) no longer
includes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SeenRecordsState:
    version: int = 1
    initialized_at_utc: str | None = None
    ipo_identities: set[str] = field(default_factory=set)
    application_identities: set[str] = field(default_factory=set)


def load_state(path: Path) -> tuple[SeenRecordsState, bool]:
    """Returns ``(state, is_first_run)``. ``is_first_run`` is True exactly
    when ``path`` didn't exist yet — an empty-but-existing file is not a
    first run."""
    if not path.exists():
        return SeenRecordsState(), True

    data = json.loads(path.read_text(encoding="utf-8"))
    state = SeenRecordsState(
        version=data.get("version", 1),
        initialized_at_utc=data.get("initialized_at_utc"),
        ipo_identities=set(data.get("ipo_identities", [])),
        application_identities=set(data.get("application_identities", [])),
    )
    return state, False


def save_state(path: Path, state: SeenRecordsState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state.version,
        "initialized_at_utc": state.initialized_at_utc,
        "ipo_identities": sorted(state.ipo_identities),
        "application_identities": sorted(state.application_identities),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
