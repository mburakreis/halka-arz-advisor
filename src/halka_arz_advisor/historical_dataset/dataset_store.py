"""Simple, versioned JSONL persistence for the historical dataset — one
JSON object per line, one line per :class:`~halka_arz_advisor.historical_dataset.models.HistoricalIpoSnapshot`.

Deliberately not a per-key cache like :class:`halka_arz_advisor.evds.cache.EvdsCache`
or :class:`halka_arz_advisor.ipo_outcomes.store.IpoMarketOutcomeStore`
(both one-file-per-key): this is a *dataset* meant to be loaded whole
for later analysis (pandas/``json.loads`` per line), not looked up by a
single ticker at a time. The file path is folded under
:data:`~halka_arz_advisor.historical_dataset.models.HISTORICAL_DATASET_VERSION`
so a future change to what a snapshot contains never silently mixes
with an older shape on disk.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from .models import HISTORICAL_DATASET_VERSION, HistoricalIpoSnapshot, snapshot_to_dict


def dataset_path(directory: Path) -> Path:
    return directory / HISTORICAL_DATASET_VERSION / "dataset.jsonl"


def write_dataset(snapshots: Sequence[HistoricalIpoSnapshot], directory: Path) -> Path:
    """Overwrites the dataset file with exactly ``snapshots``, one JSON
    object per line, sorted by ``spk_record_id`` for a deterministic
    diff between runs. Returns the path written."""
    path = dataset_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(snapshots, key=lambda s: s.spk_record_id)
    with path.open("w", encoding="utf-8") as f:
        for snapshot in ordered:
            f.write(json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False))
            f.write("\n")
    return path


def read_dataset(directory: Path) -> list[dict]:
    """Every persisted snapshot as a plain JSON-decoded ``dict`` (not
    reconstructed back into dataclasses — this project's dataset
    consumers, e.g. ``scripts/build_historical_ipo_dataset.py --inspect``,
    only ever aggregate/report over it, never feed it back into scoring)."""
    path = dataset_path(directory)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
