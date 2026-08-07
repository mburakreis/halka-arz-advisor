"""Resolves the decision cutoff for one IPO's historical snapshot: the
final subscription ("talep toplama") day, after which a real investor
could no longer act on any new information.

The cutoff is treated as **evaluation-boundary metadata, not a decision
feature** — it decides which already-fetched facts/disclosures a
snapshot is allowed to use, but is never itself scored or fed into
``expert_v0``. That distinction is what makes an *ex-post* official
record a legitimate cutoff source even though it would be a leak as a
scored feature: using it only to draw the boundary, after the fact,
causes no feature leakage, since no downstream scoring reads the cutoff
value itself.

Two sources are considered, in this priority order:

1. ``kap_extraction.subscription_end_date`` (see
   :class:`halka_arz_advisor.kap.extraction.ExtractedFacts`) — the same
   already-extracted, already-provenanced fact
   :mod:`halka_arz_advisor.decision.catalog`'s own ``subscription_window``
   feature reads, stated in the prospectus/investor sale announcement
   well before the subscription window itself opens. This is a genuine
   pre-cutoff fact with real document provenance (a KAP disclosure ID,
   document type, and page number — see
   :class:`~halka_arz_advisor.kap.extraction.SourceRef`), not an
   ex-post one; see :mod:`halka_arz_advisor.kap.extraction`'s own
   ``_SUBSCRIPTION_DATE_RANGE_RE`` for the real-world phrasing this
   project has confirmed live (the "Halka Arz Süresi" heading, not the
   originally-assumed "talep toplama").
2. :class:`halka_arz_advisor.spk.models.SpkIpoRecord` (SPK's
   *completed*-IPO record — an ex-post official record, so only ever
   used as cutoff metadata, never as a feature) — **checked and
   confirmed inapplicable**: its schema
   (``IlkHalkaArzVerileriBilgi``, verified directly against the live
   SPK OpenAPI document, ``components.schemas.IlkHalkaArzVerileriBilgi``
   — see ``data/raw/spk_openapi/*/swagger.json``) has no
   subscription/talep-toplama date property of any kind — only
   ``borsadaIslemGormeTarihi`` (trading-start date, a *different*,
   later, event) and various offer-size/price fields. There is
   therefore no code path for this tier today; it is documented here
   so a future SPK schema change (or a different ex-post official
   source) has an obvious, narrow place to be wired in without
   redesigning this module — see :data:`CutoffSource` and
   :attr:`CutoffResolution.source`.

If neither source resolves (missing, or ``subscription_end_date`` is
``"conflicting"`` — the prospectus and announcement disagree), the
cutoff is honestly unresolved. Never guessed from a post-offer proxy
(trading-start date, IPO-results publication date, or any other
after-the-fact signal) — those are exactly the kind of proxy this
project's own earlier `ipo_outcomes.trading_start` investigation found
unreliable (a KAP "trading_start" disclosure's own publish date turned
out to be a variable-lead-time *announcement*, not the event date
itself), and using one here would reintroduce the same failure mode for
the cutoff specifically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from ..kap.extraction import ExtractedFacts

CutoffStatus = Literal["resolved", "conflicting", "missing"]

# Which mechanism actually produced the cutoff — recorded on every
# resolution (even an unresolved one, as None) so a later reviewer can
# see exactly why, per company, without re-deriving it. Only one value
# is reachable today; see this module's docstring for tier 2's status.
CutoffSource = Literal["kap_extraction.subscription_end_date", "spk_ipo_record"]

CUTOFF_SOURCE_FIELD = "kap_extraction.subscription_end_date"


@dataclass(frozen=True, slots=True)
class CutoffResolution:
    status: CutoffStatus
    cutoff_date: date | None
    candidate_dates: tuple[date, ...]  # every distinct date observed, even when unresolved
    source: CutoffSource | None  # None exactly when status != "resolved"


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

    Tier 2 (an ex-post SPK record date) has no field to read — see this
    module's docstring — so it is not attempted here; a caller with a
    future second source should extend this function, not bypass it.
    """
    if facts is None:
        return CutoffResolution(status="missing", cutoff_date=None, candidate_dates=(), source=None)

    fact = facts.subscription_end_date
    if fact.status == "extracted":
        value = fact.value
        assert isinstance(value, date)
        return CutoffResolution(
            status="resolved", cutoff_date=value, candidate_dates=(value,), source="kap_extraction.subscription_end_date"
        )

    if fact.status == "conflicting":
        candidates = tuple(sorted({obs.value for obs in fact.observations if isinstance(obs.value, date)}))
        return CutoffResolution(status="conflicting", cutoff_date=None, candidate_dates=candidates, source=None)

    return CutoffResolution(status="missing", cutoff_date=None, candidate_dates=(), source=None)
