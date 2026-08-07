"""Point-in-time filters: given a resolved cutoff, decide exactly which
already-fetched pieces of data a real investor could have seen by then.

Every filter here is a pure function over data this project's existing
pipeline already produced — no new fetch, no new source. See
:mod:`halka_arz_advisor.historical_dataset`'s module docstring for why
a KAP disclosure's ``published_at`` (and an SPK application's
``application_date``) are the only provenance signals this project can
actually stand behind.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time

from ..evds.cache import EvdsCache
from ..evds.features import build_market_context_snapshot
from ..evds.models import MarketContextSnapshot
from ..kap.models import KapDisclosure
from ..spk.application_list import SpkIpoApplicationRecord

# KAP's own publishDate (see halka_arz_advisor.kap.models.parse_disclosure)
# is a naive "%d.%m.%Y %H:%M:%S" timestamp with no timezone in the
# format at all — it is Turkey wall-clock time by construction (KAP is
# a Turkish regulatory platform). end_of_day_istanbul below stays naive
# to match it directly; no tz-aware/naive mixing is introduced.


def end_of_day_istanbul(cutoff_date: date) -> datetime:
    """The cutoff date's own final instant, Europe/Istanbul wall-clock
    time — naive, matching :attr:`halka_arz_advisor.kap.models.KapDisclosure.published_at`'s
    own convention exactly (see this module's docstring)."""
    return datetime.combine(cutoff_date, time(23, 59, 59))


def disclosures_before_cutoff(
    disclosures: Sequence[KapDisclosure], cutoff_end_of_day: datetime
) -> tuple[KapDisclosure, ...]:
    """Every disclosure whose own ``published_at`` is at or before
    ``cutoff_end_of_day`` — the sole leakage gate for KAP-sourced data.
    A disclosure with no provable publish date has none here: every
    :class:`KapDisclosure` this project produces always has one (KAP's
    own ``publishDate`` is a required field — see
    :func:`halka_arz_advisor.kap.models.parse_disclosure`), so there is
    no "unknown date -> keep it anyway" case to handle."""
    return tuple(d for d in disclosures if d.published_at <= cutoff_end_of_day)


def application_record_before_cutoff(
    application_record: SpkIpoApplicationRecord | None, cutoff_date: date
) -> SpkIpoApplicationRecord | None:
    if application_record is None:
        return None
    return application_record if application_record.application_date <= cutoff_date else None


# The four EVDS series halka_arz_advisor.evds.features.build_market_context_snapshot
# needs — kept in one place so a registry change there can't silently
# leave one series unsliced here.
_EVDS_SERIES_KEYS = ("bist100_index", "policy_rate", "tlref_rate", "cpi_index")


def market_context_as_of(evds_cache: EvdsCache, cutoff_date: date) -> MarketContextSnapshot:
    """Rebuilds the exact snapshot :func:`halka_arz_advisor.evds.features.build_market_context_snapshot`
    would have produced using only observations dated on or before
    ``cutoff_date`` — never re-fetches EVDS (reads whatever
    ``evds_cache`` already has cached), and never includes an
    observation from after the cutoff, however small the lookahead."""
    sliced = {
        key: tuple(o for o in evds_cache.get_observations(key) if o.observation_date <= cutoff_date)
        for key in _EVDS_SERIES_KEYS
    }
    return build_market_context_snapshot(
        bist100_index=sliced["bist100_index"],
        policy_rate_observations=sliced["policy_rate"],
        tlref_observations=sliced["tlref_rate"],
        cpi_observations=sliced["cpi_index"],
    )
