"""Disk cache of EVDS observations — one JSON file per series key,
namespaced under the current :data:`~halka_arz_advisor.evds.registry.EVDS_REGISTRY_VERSION`
so a registry correction (a series code, source, or unit changing)
never silently mixes observations cached under the old assumptions with
ones fetched under the new one.

Observations are treated as immutable once cached (see
:class:`~halka_arz_advisor.evds.models.EvdsObservation`'s own
docstring): :meth:`EvdsCache.merge_and_save` only ever *adds* dates this
cache has never seen before for that series — an existing date's cached
value is never replaced by a later fetch, even if TCMB/TÜİK later
revises the published figure. This also gives "avoid unnecessary
repeated requests" for free: :meth:`EvdsCache.next_fetch_start_date`
tells a caller (see :mod:`halka_arz_advisor.evds.refresh`) exactly
where the already-cached range ends, so a refresh only ever requests
the gap.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .models import EvdsObservation
from .registry import EVDS_REGISTRY_VERSION


def _observation_to_dict(obs: EvdsObservation) -> dict:
    return {
        "series_code": obs.series_code,
        "observation_date": obs.observation_date.isoformat(),
        "value": obs.value,
        "unit": obs.unit,
        "frequency": obs.frequency,
        "source_institution": obs.source_institution,
        "fetched_at": obs.fetched_at.isoformat(),
    }


def _observation_from_dict(data: dict) -> EvdsObservation:
    return EvdsObservation(
        series_code=data["series_code"],
        observation_date=date.fromisoformat(data["observation_date"]),
        value=data["value"],
        unit=data["unit"],
        frequency=data["frequency"],
        source_institution=data["source_institution"],
        fetched_at=datetime.fromisoformat(data["fetched_at"]),
    )


class EvdsCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory / EVDS_REGISTRY_VERSION

    def _path(self, series_key: str) -> Path:
        return self.directory / f"{series_key}.json"

    def get_observations(self, series_key: str) -> tuple[EvdsObservation, ...]:
        path = self._path(series_key)
        if not path.exists():
            return ()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return tuple(sorted((_observation_from_dict(d) for d in raw), key=lambda o: o.observation_date))

    def latest_observation_date(self, series_key: str) -> date | None:
        observations = self.get_observations(series_key)
        return observations[-1].observation_date if observations else None

    def merge_and_save(self, series_key: str, new_observations: list[EvdsObservation]) -> int:
        """Adds only dates not already cached for ``series_key``.
        Returns how many genuinely new observations were added."""
        existing = {obs.observation_date: obs for obs in self.get_observations(series_key)}
        added = 0
        for obs in new_observations:
            if obs.observation_date not in existing:
                existing[obs.observation_date] = obs
                added += 1
        if added:
            self.directory.mkdir(parents=True, exist_ok=True)
            ordered = sorted(existing.values(), key=lambda o: o.observation_date)
            self._path(series_key).write_text(
                json.dumps([_observation_to_dict(o) for o in ordered], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        return added
