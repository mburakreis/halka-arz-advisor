"""Parse a raw EVDS JSON response into :class:`~halka_arz_advisor.evds.models.EvdsObservation` records.

EVDS's own response shape (confirmed live, 2026-08-07): a top-level
``{"items": [...]}`` object, one dict per observation date, one column
per requested series keyed by the series code with every ``.`` replaced
by ``_`` (e.g. ``TP.MK.F.BILESIK`` -> ``TP_MK_F_BILESIK``). The date
column (``"Tarih"``) is formatted differently per frequency — confirmed
live as ``"06-08-2026"`` (``DD-MM-YYYY``) for daily/business-day series
and ``"2026-7"`` (``YYYY-M``, no leading zero) for monthly ones. A cell
is ``null`` for a date the series hasn't published yet — skipped here,
never zero-filled or otherwise invented.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime

from .models import EvdsObservation
from .registry import EvdsSeriesSpec

_DAILY_DATE_RE = re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")
_MONTHLY_DATE_RE = re.compile(r"^\d{4}-\d{1,2}$")


def _parse_observation_date(raw: str, frequency: str) -> date | None:
    raw = raw.strip()
    if frequency == "daily" and _DAILY_DATE_RE.match(raw):
        return datetime.strptime(raw, "%d-%m-%Y").date()
    if frequency == "monthly" and _MONTHLY_DATE_RE.match(raw):
        year_str, month_str = raw.split("-")
        return date(int(year_str), int(month_str), 1)
    return None


def parse_evds_items(
    items: list[dict],
    series_specs: Sequence[EvdsSeriesSpec],
    *,
    fetched_at: datetime,
) -> dict[str, list[EvdsObservation]]:
    """Group raw EVDS ``items`` into a per-series-key list of
    :class:`EvdsObservation`, keyed by :attr:`EvdsSeriesSpec.key` — one
    entry per ``series_specs`` element, even if empty. A ``null``/
    unparsable cell for a given date is silently skipped for that
    series only (other series in the same response are unaffected)."""
    by_key: dict[str, list[EvdsObservation]] = {spec.key: [] for spec in series_specs}
    column_by_key = {spec.key: spec.series_code.replace(".", "_") for spec in series_specs}

    for item in items:
        raw_date = item.get("Tarih")
        if not isinstance(raw_date, str):
            continue
        for spec in series_specs:
            raw_value = item.get(column_by_key[spec.key])
            if raw_value is None:
                continue
            observation_date = _parse_observation_date(raw_date, spec.frequency)
            if observation_date is None:
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            by_key[spec.key].append(
                EvdsObservation(
                    series_code=spec.series_code,
                    observation_date=observation_date,
                    value=value,
                    unit=spec.unit,
                    frequency=spec.frequency,
                    source_institution=spec.source_institution,
                    fetched_at=fetched_at,
                )
            )
    return by_key
