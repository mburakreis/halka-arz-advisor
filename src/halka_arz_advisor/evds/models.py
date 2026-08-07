"""Data shapes for EVDS-sourced market-context data.

:class:`EvdsObservation` is the cached, immutable unit — once fetched
and cached for a given ``(series_code, observation_date)``, a later
refresh never overwrites it (see :mod:`halka_arz_advisor.evds.cache`);
TCMB/TÜİK republishing a revised figure for an already-cached date is
out of scope here, matching how this project treats every other cached
source document as fixed once observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class EvdsObservation:
    series_code: str
    observation_date: date
    value: float
    unit: str
    frequency: str
    source_institution: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class MarketContextFeatureValue:
    """One derived market-context feature's value — what
    :mod:`halka_arz_advisor.decision.audit` reads for a
    ``market_data.<name>`` field once :mod:`halka_arz_advisor.evds` has
    produced a snapshot."""

    value: float
    as_of_date: date
    source_series_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketContextSnapshot:
    """Every derived feature this project currently exposes to the
    decision coverage audit's ``market_context`` category — see
    :mod:`halka_arz_advisor.evds.features` for how each is computed.
    Deliberately company-agnostic: one snapshot is shared across every
    company evaluated in a given run (market context isn't specific to
    any one IPO)."""

    features: dict[str, MarketContextFeatureValue] = field(default_factory=dict)

    def get(self, name: str) -> MarketContextFeatureValue | None:
        return self.features.get(name)
