"""Resolves the decision cutoff for one IPO's historical snapshot: the
final subscription ("talep toplama") day, after which a real investor
could no longer act on any new information.

Sourced exclusively from ``kap_extraction.subscription_end_date`` (see
:class:`halka_arz_advisor.kap.extraction.ExtractedFacts`) — the same
already-extracted, already-provenanced fact
:mod:`halka_arz_advisor.decision.catalog`'s own ``subscription_window``
feature reads, stated in the prospectus/investor sale announcement well
before the subscription window itself opens. Deliberately **not**
sourced from :class:`halka_arz_advisor.spk.models.SpkIpoRecord`: that
record is SPK's *completed*-IPO listing, with no per-record publish
timestamp this project can point to — using it to derive a cutoff would
be exactly the kind of unprovable-availability leak this dataset must
avoid (see :mod:`halka_arz_advisor.historical_dataset`'s module
docstring). If ``subscription_end_date`` is missing or conflicting
(prospectus and announcement disagree), the cutoff is honestly
unresolved — never guessed from a nearby date (e.g. trading-start minus
a few days).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from ..kap.extraction import ExtractedFacts

CutoffStatus = Literal["resolved", "conflicting", "missing"]

CUTOFF_SOURCE_FIELD = "kap_extraction.subscription_end_date"


@dataclass(frozen=True, slots=True)
class CutoffResolution:
    status: CutoffStatus
    cutoff_date: date | None
    candidate_dates: tuple[date, ...]  # every distinct date observed, even when unresolved


def resolve_decision_cutoff(facts: ExtractedFacts | None) -> CutoffResolution:
    """``facts`` should be built from *every* disclosure currently
    matched to the company (not yet cutoff-filtered) — the subscription
    dates are a fixed fact stated in advance, so reading them off the
    full available document set is safe and necessary (there is no
    cutoff yet to filter by). Every disclosure is still independently
    checked against the resulting cutoff afterwards (see
    :mod:`halka_arz_advisor.historical_dataset.snapshot_builder`), so a
    prospectus that somehow post-dates its own stated subscription
    window is caught there, not assumed away here.
    """
    if facts is None:
        return CutoffResolution(status="missing", cutoff_date=None, candidate_dates=())

    fact = facts.subscription_end_date
    if fact.status == "extracted":
        value = fact.value
        assert isinstance(value, date)
        return CutoffResolution(status="resolved", cutoff_date=value, candidate_dates=(value,))

    if fact.status == "conflicting":
        candidates = tuple(sorted({obs.value for obs in fact.observations if isinstance(obs.value, date)}))
        return CutoffResolution(status="conflicting", cutoff_date=None, candidate_dates=candidates)

    return CutoffResolution(status="missing", cutoff_date=None, candidate_dates=())
