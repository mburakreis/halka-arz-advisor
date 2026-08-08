"""Canonical, provenance-preserving pre-offer economic terms for one IPO
("OfferingTerms") — a normalized *view* over
:class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`, not a new
extraction pipeline: every scalar field here is either a direct
passthrough of an already-extracted ``kap_extraction`` fact, or derived
from two or more such facts using only plain, auditable arithmetic (see
:func:`_derive`).

Only pre-offer-safe official sources feed this model — the same
``approved_prospectus``/``investor_sale_announcement`` document types
:mod:`halka_arz_advisor.kap.extraction` already scopes field extraction
to (see ``kap.documents._EXTRACTION_ELIGIBLE_TYPES``). Nothing here
reads ``ipo_results``/``price_determination_report`` (post-offer/
valuation-summary document types) or
:class:`halka_arz_advisor.spk.models.SpkIpoRecord` (SPK's completed-IPO
record, only populated once an IPO is complete) — using either to fill
a missing pre-offer value would be exactly the leakage this model
exists to avoid.

Every field carries its own status, using the same three-value
vocabulary :class:`~halka_arz_advisor.kap.extraction.ExtractedFact`
already uses (never a new taxonomy):

- ``"extracted"`` — one or more agreeing observations, or (for a
  derived field) every input successfully resolved.
- ``"conflicting"`` — two or more disagreeing observations for a direct
  field, or (for a derived field) a required input is itself
  conflicting. Never silently arbitrated — no value is picked, and a
  conflicting *input* propagates to a conflicting *output*, never a
  guess.
- ``"not_found"`` — nothing found for a direct field, or a derived
  field's required input is genuinely unavailable (including the
  degenerate case ``par_value == 0``, which would make share-count
  division meaningless).

``derived`` (a plain ``bool``, not folded into ``status``) distinguishes
a directly-stated value from one this module computed — so a caller can
tell "the document said this" from "this project's own arithmetic says
this" without losing the extracted/conflicting/not_found status
vocabulary.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .extraction import AllocationLineItem, ExtractedFact, ExtractedFacts, FieldObservation, InvestorGroup, merge_field_observations
from .models import KapDisclosure

OfferingTermStatus = Literal["extracted", "conflicting", "not_found"]


@dataclass(frozen=True, slots=True)
class OfferingTermObservation:
    """One value as found in (or computed from) one specific document —
    the same shape as
    :class:`~halka_arz_advisor.kap.extraction.FieldObservation`, plus
    the document's own publication timestamp (not carried by
    :class:`~halka_arz_advisor.kap.extraction.SourceRef`, which only
    identifies *which* document, not *when* it was published)."""

    value: object
    raw_snippet: str | None
    source_document_type: str | None
    source_disclosure_id: str | None
    source_published_at: datetime | None
    source_page_number: int | None
    extraction_method: str | None
    source_system: str | None


@dataclass(frozen=True, slots=True)
class OfferingTermField:
    """One ``OfferingTerms`` field: value/unit, status, and every
    observation (direct or, for a derived field, every dependency's own
    observations) that fed it — see the module docstring for the status/
    ``derived`` vocabulary."""

    status: OfferingTermStatus
    value: object | None
    unit: str | None
    derived: bool
    observations: tuple[OfferingTermObservation, ...]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class OfferingTerms:
    offer_price: OfferingTermField
    subscription_start: OfferingTermField
    subscription_end: OfferingTermField
    total_offered_shares: OfferingTermField
    new_issue_shares: OfferingTermField
    secondary_sale_shares: OfferingTermField
    pre_offer_share_count: OfferingTermField
    post_offer_share_count: OfferingTermField
    gross_offer_size: OfferingTermField
    implied_post_money_market_cap: OfferingTermField
    distribution_method: OfferingTermField
    retail_allocation_percentage: OfferingTermField
    retail_offered_shares: OfferingTermField
    investor_group_allocations: OfferingTermField  # value: tuple[AllocationLineItem, ...] | None


OFFERING_TERM_FIELD_NAMES: tuple[str, ...] = (
    "offer_price",
    "subscription_start",
    "subscription_end",
    "total_offered_shares",
    "new_issue_shares",
    "secondary_sale_shares",
    "pre_offer_share_count",
    "post_offer_share_count",
    "gross_offer_size",
    "implied_post_money_market_cap",
    "distribution_method",
    "retail_allocation_percentage",
    "retail_offered_shares",
    "investor_group_allocations",
)


def _to_offering_observation(obs: FieldObservation, disclosures_by_id: dict[str, KapDisclosure]) -> OfferingTermObservation:
    disclosure = disclosures_by_id.get(obs.source.disclosure_id) if obs.source else None
    return OfferingTermObservation(
        value=obs.value,
        raw_snippet=obs.raw_snippet,
        source_document_type=obs.source.document_type if obs.source else None,
        source_disclosure_id=obs.source.disclosure_id if obs.source else None,
        source_published_at=disclosure.published_at if disclosure else None,
        source_page_number=obs.source.page_number if obs.source else None,
        extraction_method=obs.source.extraction_method if obs.source else None,
        source_system=obs.source.source_system if obs.source else None,
    )


def _pre_offer_safe_fact(fact: ExtractedFact, field_name: str) -> ExtractedFact:
    """Re-derive ``fact`` using only its ``approved_prospectus``/
    ``investor_sale_announcement``-sourced observations, discarding any
    ``ipo_results``/``price_determination_report``-sourced one.

    :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts` is a
    *shared* model — for a field name like ``total_offered_shares``
    that's also in scope for the (post-offer) IPO-results notice's own
    extractor pass (``kap.extraction._SCALAR_EXTRACTORS`` is not scoped
    per document type), ``merge_field_observations`` already allows an
    ``ipo_results_observation`` to merge into the very same field slot a
    prospectus/announcement observation uses — confirmed live (2026-08-08)
    against a real conflict this project's own IPO-results notice
    introduced. That's a legitimate reading of a *shared* fact for other
    consumers, but ``OfferingTerms`` is specifically pre-offer-safe by
    contract, so it re-runs the same, already-tested
    :func:`~halka_arz_advisor.kap.extraction.merge_field_observations`
    logic scoped to only the two pre-offer-safe observations, rather
    than trusting the already-merged (potentially post-offer-tainted)
    ``fact.status``/``fact.value`` directly.
    """
    prospectus_obs = next(
        (o for o in fact.observations if o.source and o.source.document_type == "approved_prospectus"), None
    )
    announcement_obs = next(
        (o for o in fact.observations if o.source and o.source.document_type == "investor_sale_announcement"), None
    )
    return merge_field_observations(field_name, prospectus_obs, announcement_obs)


def _passthrough(fact: ExtractedFact, field_name: str, unit: str | None, disclosures_by_id: dict[str, KapDisclosure]) -> OfferingTermField:
    safe_fact = _pre_offer_safe_fact(fact, field_name)
    observations = tuple(_to_offering_observation(o, disclosures_by_id) for o in safe_fact.observations)
    return OfferingTermField(status=safe_fact.status, value=safe_fact.value, unit=unit, derived=False, observations=observations)


def _blocked(status: OfferingTermStatus, unit: str | None, deps: Sequence[OfferingTermField], notes: str) -> OfferingTermField:
    observations = tuple(obs for dep in deps for obs in dep.observations)
    return OfferingTermField(status=status, value=None, unit=unit, derived=True, observations=observations, notes=notes)


def _derive(op: Callable[..., float | None], unit: str | None, *deps: OfferingTermField, blocked_notes: str) -> OfferingTermField:
    """Combine ``deps`` (each already resolved) into one derived field:
    a conflicting dependency propagates to a conflicting result (never
    arbitrated), a missing dependency propagates to not_found, and only
    when every dependency is itself resolved (``"extracted"``) is ``op``
    actually applied. ``op`` returning ``None`` (e.g. division by a zero
    par value) is treated the same as a missing dependency — a
    computation that can't produce a meaningful number is reported
    ``not_found``, never a bogus value."""
    if any(d.status == "conflicting" for d in deps):
        return _blocked("conflicting", unit, deps, f"blocked: {blocked_notes} — a required input is conflicting")
    if any(d.status == "not_found" for d in deps):
        return _blocked("not_found", unit, deps, f"unavailable: {blocked_notes} — a required input was not found")
    value = op(*(d.value for d in deps))
    if value is None:
        return _blocked("not_found", unit, deps, f"unavailable: {blocked_notes} — computation did not produce a value")
    observations = tuple(obs for dep in deps for obs in dep.observations)
    return OfferingTermField(status="extracted", value=value, unit=unit, derived=True, observations=observations)


def _secondary_sale_field(
    facts: ExtractedFacts,
    disclosures_by_id: dict[str, KapDisclosure],
    total_offered: OfferingTermField,
    new_issue: OfferingTermField,
) -> OfferingTermField:
    """Prefer a directly-stated secondary-sale figure (see
    :func:`~halka_arz_advisor.kap.extraction.extract_secondary_sale_shares`);
    only when nothing was directly found (never when it's genuinely
    conflicting — that stays conflicting, not silently overridden) fall
    back to ``total_offered_shares − new_issue_shares``, verified live
    against two real multi-seller documents (see that function's
    docstring: EMPAE 9,000,000 = 38,000,000 − 29,000,000; EKDMR
    12,000,000 = 52,000,000 − 40,000,000)."""
    direct = _passthrough(facts.secondary_sale_shares, "secondary_sale_shares", "shares", disclosures_by_id)
    if direct.status != "not_found":
        return direct
    return _derive(
        lambda total, increase: total - increase,
        "shares",
        total_offered,
        new_issue,
        blocked_notes="derived as total_offered_shares - new_issue_shares",
    )


def _share_count_field(capital: OfferingTermField, par_value: OfferingTermField, label: str) -> OfferingTermField:
    def _divide(capital_amount: float, par: float) -> float | None:
        if par == 0:
            return None
        return capital_amount / par

    return _derive(_divide, "shares", capital, par_value, blocked_notes=f"derived as {label} / par_value_per_share")


def _retail_allocation_fields(allocations: OfferingTermField) -> tuple[OfferingTermField, OfferingTermField]:
    """``retail_allocation_percentage``/``retail_offered_shares`` — read
    off the ``"retail"``-classified entry of
    :data:`~halka_arz_advisor.kap.extraction.investor_group_allocations`
    (never inferred any other way: if the allocation table wasn't found,
    or was found but states no retail line — a genuinely different
    distribution structure, not a parsing failure — both fields report
    accordingly, never guessed)."""
    if allocations.status != "extracted":
        pct = OfferingTermField(
            status=allocations.status, value=None, unit="percent", derived=True,
            observations=allocations.observations, notes="derived from investor_group_allocations",
        )
        shares = OfferingTermField(
            status=allocations.status, value=None, unit="shares", derived=True,
            observations=allocations.observations, notes="derived from investor_group_allocations",
        )
        return pct, shares

    items: tuple[AllocationLineItem, ...] = allocations.value or ()
    retail = next((item for item in items if item.group == "retail"), None)
    if retail is None:
        pct = OfferingTermField(
            status="not_found", value=None, unit="percent", derived=True,
            observations=allocations.observations, notes="no retail-classified group line in the allocation table",
        )
        shares = OfferingTermField(
            status="not_found", value=None, unit="shares", derived=True,
            observations=allocations.observations, notes="no retail-classified group line in the allocation table",
        )
        return pct, shares

    pct = OfferingTermField(
        status="extracted" if retail.percentage is not None else "not_found",
        value=retail.percentage, unit="percent", derived=True, observations=allocations.observations,
    )
    shares = OfferingTermField(
        status="extracted" if retail.amount_try is not None else "not_found",
        value=retail.amount_try, unit="shares", derived=True, observations=allocations.observations,
    )
    return pct, shares


def build_offering_terms(facts: ExtractedFacts | None, disclosures: Sequence[KapDisclosure] = ()) -> OfferingTerms:
    """Build one company's canonical pre-offer ``OfferingTerms`` from its
    already-merged :class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`
    (see :func:`halka_arz_advisor.kap.documents.aggregate_company_facts`)
    — this function performs no I/O and reads no document text itself.

    ``disclosures`` should be whatever set of
    :class:`~halka_arz_advisor.kap.models.KapDisclosure` ``facts`` was
    built from (for a leakage-safe historical snapshot, the same
    cutoff-filtered set the caller already resolved elsewhere — this
    function has no cutoff logic of its own and trusts the caller's
    document selection completely) — used only to attach each
    observation's publication timestamp; a ``facts`` built from
    disclosures not present here still works, just without that one
    piece of provenance metadata.
    """
    empty_field = OfferingTermField(status="not_found", value=None, unit=None, derived=False, observations=())
    if facts is None:
        return OfferingTerms(**{name: empty_field for name in OFFERING_TERM_FIELD_NAMES})

    disclosures_by_id = {d.disclosure_id: d for d in disclosures}

    offer_price = _passthrough(facts.offering_price, "offering_price", "TRY", disclosures_by_id)
    subscription_start = _passthrough(facts.subscription_start_date, "subscription_start_date", "date", disclosures_by_id)
    subscription_end = _passthrough(facts.subscription_end_date, "subscription_end_date", "date", disclosures_by_id)
    total_offered_shares = _passthrough(facts.total_offered_shares, "total_offered_shares", "shares", disclosures_by_id)
    new_issue_shares = _passthrough(facts.capital_increase_shares, "capital_increase_shares", "shares", disclosures_by_id)
    distribution_method = _passthrough(facts.distribution_method, "distribution_method", None, disclosures_by_id)
    par_value = _passthrough(facts.par_value_per_share, "par_value_per_share", "TRY_per_share", disclosures_by_id)
    pre_offer_capital = _passthrough(facts.pre_offer_capital, "pre_offer_capital", "TRY", disclosures_by_id)
    post_offer_capital = _passthrough(facts.post_offer_capital, "post_offer_capital", "TRY", disclosures_by_id)
    investor_group_allocations = _passthrough(facts.investor_group_allocations, "investor_group_allocations", None, disclosures_by_id)

    secondary_sale_shares = _secondary_sale_field(facts, disclosures_by_id, total_offered_shares, new_issue_shares)

    pre_offer_share_count = _share_count_field(pre_offer_capital, par_value, "pre_offer_capital")
    post_offer_share_count = _share_count_field(post_offer_capital, par_value, "post_offer_capital")

    gross_offer_size = _derive(
        lambda price, shares: price * shares,
        "TRY",
        offer_price,
        total_offered_shares,
        blocked_notes="derived as offer_price * total_offered_shares",
    )
    implied_post_money_market_cap = _derive(
        lambda price, shares: price * shares,
        "TRY",
        offer_price,
        post_offer_share_count,
        blocked_notes="derived as offer_price * post_offer_share_count",
    )

    retail_allocation_percentage, retail_offered_shares = _retail_allocation_fields(investor_group_allocations)

    return OfferingTerms(
        offer_price=offer_price,
        subscription_start=subscription_start,
        subscription_end=subscription_end,
        total_offered_shares=total_offered_shares,
        new_issue_shares=new_issue_shares,
        secondary_sale_shares=secondary_sale_shares,
        pre_offer_share_count=pre_offer_share_count,
        post_offer_share_count=post_offer_share_count,
        gross_offer_size=gross_offer_size,
        implied_post_money_market_cap=implied_post_money_market_cap,
        distribution_method=distribution_method,
        retail_allocation_percentage=retail_allocation_percentage,
        retail_offered_shares=retail_offered_shares,
        investor_group_allocations=investor_group_allocations,
    )


def offering_term_field_as_dict(field_value: OfferingTermField) -> dict:
    return {
        "status": field_value.status,
        "value": _json_safe(field_value.value),
        "unit": field_value.unit,
        "derived": field_value.derived,
        "notes": field_value.notes,
        "observations": [
            {
                "value": _json_safe(obs.value),
                "raw_snippet": obs.raw_snippet,
                "source_document_type": obs.source_document_type,
                "source_disclosure_id": obs.source_disclosure_id,
                "source_published_at": obs.source_published_at.isoformat() if obs.source_published_at else None,
                "source_page_number": obs.source_page_number,
                "extraction_method": obs.extraction_method,
                "source_system": obs.source_system,
            }
            for obs in field_value.observations
        ],
    }


def offering_terms_as_dict(terms: OfferingTerms) -> dict:
    return {name: offering_term_field_as_dict(getattr(terms, name)) for name in OFFERING_TERM_FIELD_NAMES}


def _json_safe(value: object) -> object:
    if isinstance(value, AllocationLineItem):
        return {
            "group": value.group,
            "group_label_raw": value.group_label_raw,
            "amount_try": value.amount_try,
            "percentage": value.percentage,
        }
    if isinstance(value, (tuple, list)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = [
    "InvestorGroup",
    "OFFERING_TERM_FIELD_NAMES",
    "OfferingTermField",
    "OfferingTermObservation",
    "OfferingTermStatus",
    "OfferingTerms",
    "build_offering_terms",
    "offering_term_field_as_dict",
    "offering_terms_as_dict",
]
