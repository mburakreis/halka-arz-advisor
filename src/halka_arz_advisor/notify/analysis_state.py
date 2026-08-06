"""Persisted "have we already sent this Gemini analysis?" state.

Separate from :mod:`halka_arz_advisor.notify.state`'s SPK
notification-state file (different question, different file) — this
tracks delivered Gemini analyses by a content hash (see
:mod:`halka_arz_advisor.notify.analysis_identity`), not a raw record
identity, so a *changed* analysis for an already-notified company is
correctly treated as new and resent rather than being suppressed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SentAnalysesState:
    version: int = 1
    initialized_at_utc: str | None = None
    sent_hashes: set[str] = field(default_factory=set)


def load_state(path: Path) -> tuple[SentAnalysesState, bool]:
    """Returns ``(state, is_first_run)``. ``is_first_run`` is True exactly
    when ``path`` didn't exist yet — an empty-but-existing file is not a
    first run."""
    if not path.exists():
        return SentAnalysesState(), True

    data = json.loads(path.read_text(encoding="utf-8"))
    state = SentAnalysesState(
        version=data.get("version", 1),
        initialized_at_utc=data.get("initialized_at_utc"),
        sent_hashes=set(data.get("sent_hashes", [])),
    )
    return state, False


def save_state(path: Path, state: SentAnalysesState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": state.version,
        "initialized_at_utc": state.initialized_at_utc,
        "sent_hashes": sorted(state.sent_hashes),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
