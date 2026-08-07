"""Disk store for :class:`~halka_arz_advisor.ipo_outcomes.models.IpoMarketOutcome`
— one JSON file per ticker, mirroring :class:`halka_arz_advisor.evds.cache.EvdsCache`'s
shape. Unlike that cache, a re-run *does* overwrite a ticker's existing
entry (via :meth:`IpoMarketOutcomeStore.put`): unlike an immutable
published bulletin day, an outcome computed while the IPO is still
within one of its return windows is expected to be recomputed with more
data as later trading days accumulate.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import IpoMarketOutcome, outcome_from_dict, outcome_to_dict


class IpoMarketOutcomeStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, ticker: str) -> Path:
        return self.directory / f"{ticker.upper()}.json"

    def get(self, ticker: str) -> IpoMarketOutcome | None:
        path = self._path(ticker)
        if not path.exists():
            return None
        return outcome_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def put(self, outcome: IpoMarketOutcome) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(outcome.ticker).write_text(
            json.dumps(outcome_to_dict(outcome), indent=2, ensure_ascii=False), encoding="utf-8"
        )
