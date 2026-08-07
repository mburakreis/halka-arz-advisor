"""Resolves the decision cutoff for one IPO's historical snapshot: the
final subscription ("talep toplama") day, after which a real investor
could no longer act on any new information.

The cutoff is treated as **evaluation-boundary metadata, not a decision
feature** — it decides which already-fetched facts/disclosures a
snapshot is allowed to use, but is never itself scored or fed into
``expert_v0``. That distinction is what makes an *ex-post* official
document a legitimate cutoff source even though it would be a leak as a
scored feature: using it only to draw the boundary, after the fact,
causes no feature leakage, since no downstream scoring reads the cutoff
value itself — but **only** when that document explicitly restates the
actual subscription/sale date range; nothing else it contains is ever
read for this purpose, and none of its other facts are permitted to
enter a historical snapshot's features (see
:mod:`halka_arz_advisor.historical_dataset.post_offer_evidence`, which
keeps this structurally separate: it reads raw document text directly
for exactly this one purpose, never through
:mod:`halka_arz_advisor.kap.documents`'s normal
:class:`~halka_arz_advisor.kap.extraction.ExtractedFacts` pipeline that
feeds decision-engine scoring).

Three sources are considered, in this priority order — a higher tier
that resolves (or conflicts) is final; a lower tier is only consulted
when the tier above it found **no evidence at all**:

1. ``kap_extraction.subscription_end_date`` (see
   :class:`halka_arz_advisor.kap.extraction.ExtractedFacts`) — the same
   already-extracted, already-provenanced fact
   :mod:`halka_arz_advisor.decision.catalog`'s own ``subscription_window``
   feature reads, stated in the prospectus/investor sale announcement
   well before the subscription window itself opens. A genuine
   pre-cutoff fact with real document provenance, not an ex-post one.
2. An explicit historical subscription date range **restated** in an
   official KAP post-offer disclosure — today, specifically the
   "Halka Arzı Sonuçları" (IPO-results) notice (see
   :func:`halka_arz_advisor.kap.extraction.extract_subscription_end_date_from_result_text`).
   Confirmed live to reliably restate the closing subscription date in
   its own opening sentence, in a Turkish calendar-date form
   (e.g. "29 - 30 Haziran, 1 Temmuz 2026 tarihleri arasında talep
   toplanmıştır") independent of — and often clearer than — the
   original announcement's own OCR quality.
3. The same explicit date range, restated in an official issuer-IR copy
   of the relevant pre-offer document (an investor sale announcement or
   prospectus mirrored on the issuer's own site — see
   :mod:`halka_arz_advisor.issuer_ir`) — used even though that copy's
   own crawl timestamp is unreliable as *feature* provenance (see
   :mod:`halka_arz_advisor.historical_dataset`'s module docstring for
   why issuer-IR documents are never used as feature evidence), because
   here it is only cutoff evidence, not a feature.

:class:`halka_arz_advisor.spk.models.SpkIpoRecord` (SPK's *completed*-
IPO record) was checked and confirmed **not** a viable source at any
tier: its schema (``IlkHalkaArzVerileriBilgi``, verified directly
against the live SPK OpenAPI document,
``components.schemas.IlkHalkaArzVerileriBilgi`` — see
``data/raw/spk_openapi/*/swagger.json``) has no subscription/talep-
toplama date property of any kind — only ``borsadaIslemGormeTarihi``
(trading-start date, a different, later event) and various offer-size/
price fields.

If no tier resolves (missing everywhere, or a tier's own evidence
disagrees with itself), the cutoff is honestly unresolved. Never
guessed from a post-offer *proxy* (IPO-results **publication** date,
trading-start date, announcement publication date, or any other
after-the-fact timestamp/offset) — only from a document's own explicit
textual statement of the date range itself. This project's own earlier
``ipo_outcomes.trading_start`` investigation found exactly this kind of
proxy unreliable (a KAP "trading_start" disclosure's own publish date
turned out to be a variable-lead-time *announcement*, not the event
date itself); using a publish/crawl timestamp as a cutoff proxy here
would reintroduce the same failure mode.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from ..kap.extraction import ExtractedFacts

CutoffStatus = Literal["resolved", "conflicting", "missing"]

CutoffSource = Literal[
    "kap_extraction.subscription_end_date",
    "kap_ipo_results.subscription_end_date",
    "issuer_ir.subscription_end_date",
]

CUTOFF_SOURCE_FIELD = "kap_extraction.subscription_end_date"


@dataclass(frozen=True, slots=True)
class PostOfferCutoffEvidence:
    """One post-offer document's own explicit restatement of the
    subscription date range, already extracted — pure data, no I/O —
    so :func:`resolve_decision_cutoff` stays free of any PDF/OCR
    concern. Built by
    :mod:`halka_arz_advisor.historical_dataset.post_offer_evidence`,
    never by anything that also touches
    :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`."""

    cutoff_date: date
    source: CutoffSource  # "kap_ipo_results.subscription_end_date" or "issuer_ir.subscription_end_date"
    disclosure_id: str
    snippet: str


@dataclass(frozen=True, slots=True)
class CutoffResolution:
    status: CutoffStatus
    cutoff_date: date | None
    candidate_dates: tuple[date, ...]  # every distinct date observed, even when unresolved
    source: CutoffSource | None  # None exactly when status != "resolved"
    evidence_disclosure_id: str | None = None  # which document resolved it, when known


def _from_post_offer_evidence(evidence: Sequence[PostOfferCutoffEvidence], source: CutoffSource) -> CutoffResolution | None:
    """``None`` when ``evidence`` (already filtered to one ``source``)
    is empty — the caller should fall through to the next tier, not
    treat this as an unresolved final answer."""
    matching = [e for e in evidence if e.source == source]
    if not matching:
        return None
    dates = sorted({e.cutoff_date for e in matching})
    if len(dates) == 1:
        # All matching evidence agrees on this one date — any of them
        # names a valid supporting document; pick deterministically by
        # disclosure_id rather than input order.
        winner = min(matching, key=lambda e: e.disclosure_id)
        return CutoffResolution(
            status="resolved", cutoff_date=dates[0], candidate_dates=tuple(dates), source=source,
            evidence_disclosure_id=winner.disclosure_id,
        )
    # More than one document at this tier states a different date —
    # preserved, never guessed between them, and never overridden by a
    # lower-priority tier (a real disagreement within an authoritative
    # tier isn't resolved by consulting a less authoritative one).
    return CutoffResolution(status="conflicting", cutoff_date=None, candidate_dates=tuple(dates), source=None)


def resolve_decision_cutoff(
    facts: ExtractedFacts | None, *, post_offer_evidence: Sequence[PostOfferCutoffEvidence] = ()
) -> CutoffResolution:
    """``facts`` should be built from *every* disclosure currently
    matched to the company (not yet cutoff-filtered) — the subscription
    dates are a fixed fact stated in advance, so reading them off the
    full available document set is safe and necessary (there is no
    cutoff yet to filter by). Every disclosure is still independently
    checked against the resulting cutoff afterwards (see
    :mod:`halka_arz_advisor.historical_dataset.snapshot_builder`), so a
    prospectus that somehow post-dates its own stated subscription
    window is caught there, not assumed away here.

    ``post_offer_evidence`` — already-extracted, already-resolved (see
    :class:`PostOfferCutoffEvidence`) — supplies tiers 2 and 3; this
    function itself never reads a document.
    """
    if facts is not None:
        fact = facts.subscription_end_date
        if fact.status == "extracted":
            value = fact.value
            assert isinstance(value, date)
            return CutoffResolution(
                status="resolved", cutoff_date=value, candidate_dates=(value,),
                source="kap_extraction.subscription_end_date",
                evidence_disclosure_id=fact.source.disclosure_id if fact.source else None,
            )
        if fact.status == "conflicting":
            candidates = tuple(sorted({obs.value for obs in fact.observations if isinstance(obs.value, date)}))
            return CutoffResolution(status="conflicting", cutoff_date=None, candidate_dates=candidates, source=None)
        # "not_found" falls through to tier 2/3 below.

    tier2 = _from_post_offer_evidence(post_offer_evidence, "kap_ipo_results.subscription_end_date")
    if tier2 is not None:
        return tier2

    tier3 = _from_post_offer_evidence(post_offer_evidence, "issuer_ir.subscription_end_date")
    if tier3 is not None:
        return tier3

    return CutoffResolution(status="missing", cutoff_date=None, candidate_dates=(), source=None)
