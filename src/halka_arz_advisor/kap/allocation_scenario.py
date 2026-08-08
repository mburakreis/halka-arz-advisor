"""Deterministic, pre-offer retail allocation *scenario* calculator over
an already-built :class:`~halka_arz_advisor.kap.offering_terms.OfferingTerms`.

Given a **hypothetical** retail participant count supplied by the
caller, computes what a flat equal-distribution retail allocation would
look like at that hypothetical demand level. This is explicitly not a
participant-count prediction model — it never estimates, forecasts, or
guesses how many investors will actually apply; the count is always an
input the caller chooses, never an output.

Only ever reads already-resolved (``status == "extracted"``)
:class:`~halka_arz_advisor.kap.offering_terms.OfferingTermField` values.
A field that is ``"not_found"`` or ``"conflicting"`` blocks the
corresponding output rather than falling back to a guessed default (no
assumed lot size, no assumed distribution rule, no assumed offer
price) — the blocked reason is always reported explicitly in
``caveats``, never silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .offering_terms import OfferingTerms

AllocationScenarioStatus = Literal["computed", "unavailable"]


@dataclass(frozen=True, slots=True)
class AllocationScenario:
    """One hypothetical-demand scenario for the retail tranche.

    ``lots_per_investor``/``tl_allocation_per_investor`` are only
    populated when ``status == "computed"`` — i.e. ``retail_distribution_rule``
    is confirmed ``"equal"`` and ``retail_offered_shares`` is resolved
    (``offer_price`` additionally gates the TL figure). "Lot" here means
    one share at nominal (par) value — this project extracts no
    separate lot-size field, so a share count is reported directly, not
    converted through an assumed lot definition; see ``assumptions``.
    """

    hypothetical_retail_participant_count: int
    status: AllocationScenarioStatus
    lots_per_investor: float | None
    tl_allocation_per_investor: float | None
    assumptions: tuple[str, ...]
    caveats: tuple[str, ...]


def build_allocation_scenario(terms: OfferingTerms, hypothetical_retail_participant_count: int) -> AllocationScenario:
    """Pure computation — no I/O, no randomness, no participant-count
    forecasting. ``hypothetical_retail_participant_count`` is always
    supplied by the caller as a what-if input."""
    if hypothetical_retail_participant_count <= 0:
        return AllocationScenario(
            hypothetical_retail_participant_count=hypothetical_retail_participant_count,
            status="unavailable",
            lots_per_investor=None,
            tl_allocation_per_investor=None,
            assumptions=(),
            caveats=("hypothetical_retail_participant_count must be a positive integer",),
        )

    rule = terms.retail_distribution_rule
    retail_shares = terms.retail_offered_shares
    offer_price = terms.offer_price

    caveats: list[str] = []
    if rule.status == "not_found":
        caveats.append(
            "retail_distribution_rule is not_found — no official pre-offer document confirmed whether the "
            "retail tranche uses equal or proportional distribution, so no flat per-investor scenario can be computed."
        )
    elif rule.status == "conflicting":
        caveats.append(
            "retail_distribution_rule is conflicting across sources — genuinely disagreeing official evidence, "
            "not arbitrated here, so no flat per-investor scenario can be computed."
        )
    elif rule.value != "equal":
        caveats.append(
            f"retail_distribution_rule is '{rule.value}', not 'equal' — a proportional (or otherwise non-equal) "
            "distribution depends on total retail demand at subscription close, which this scenario calculator "
            "deliberately does not predict or assume."
        )

    if retail_shares.status != "extracted":
        caveats.append(f"retail_offered_shares is {retail_shares.status} — cannot compute a per-investor share count without it.")

    can_compute_lots = rule.status == "extracted" and rule.value == "equal" and retail_shares.status == "extracted"

    if offer_price.status != "extracted":
        caveats.append(f"offer_price is {offer_price.status} — cannot compute a TL allocation value without it.")

    lots_per_investor: float | None = None
    tl_allocation_per_investor: float | None = None
    assumptions: list[str] = []

    if can_compute_lots:
        lots_per_investor = retail_shares.value / hypothetical_retail_participant_count
        assumptions.append(
            "equal distribution: retail_offered_shares is split evenly across the hypothetical participant count, "
            "with no per-investor minimum-unit rounding or oversubscription-driven reallocation modeled."
        )
        assumptions.append(
            "'lot' is treated as one share at nominal (par) value — this project extracts no separate lot-size "
            "field; every real sampled document that defines a lot ties it 1:1 to a single share, but that "
            "definition is not independently re-verified per company here."
        )
        assumptions.append(
            "assumes each hypothetical investor applies independently for exactly the modeled allocation amount "
            "(the practical minimum order needed to receive it under equal distribution); it does not model "
            "whether the offering collects the order amount in full up front or against a credit line."
        )
        if offer_price.status == "extracted":
            tl_allocation_per_investor = lots_per_investor * offer_price.value

    status: AllocationScenarioStatus = "computed" if can_compute_lots else "unavailable"
    return AllocationScenario(
        hypothetical_retail_participant_count=hypothetical_retail_participant_count,
        status=status,
        lots_per_investor=lots_per_investor,
        tl_allocation_per_investor=tl_allocation_per_investor,
        assumptions=tuple(assumptions),
        caveats=tuple(caveats),
    )


def allocation_scenario_as_dict(scenario: AllocationScenario) -> dict:
    return {
        "hypothetical_retail_participant_count": scenario.hypothetical_retail_participant_count,
        "status": scenario.status,
        "lots_per_investor": scenario.lots_per_investor,
        "tl_allocation_per_investor": scenario.tl_allocation_per_investor,
        "assumptions": list(scenario.assumptions),
        "caveats": list(scenario.caveats),
    }


__all__ = [
    "AllocationScenario",
    "AllocationScenarioStatus",
    "allocation_scenario_as_dict",
    "build_allocation_scenario",
]
