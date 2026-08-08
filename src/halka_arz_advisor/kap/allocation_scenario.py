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

**``retail_offered_shares / participant_count`` is only an average, not
an exact per-investor allocation.** Even under a confirmed *equal*
distribution rule, a real IPO's actual per-investor share count is an
integer, and ``retail_offered_shares`` rarely divides evenly across
``hypothetical_retail_participant_count`` — the real remainder is
resolved by mechanics this project has no pre-offer-safe source for
(smaller/fully-satisfiable orders are typically filled first, and the
still-unallocated shares are then redistributed among the rest — see
``kap.extraction``'s own investor-group tahsisat-table docstring for
the *between-group* version of the same "not simple division" point).
So instead of pretending to compute one exact number, this module
reports the deterministic floor/remainder baseline math *and* an
explicit, permanent caveat that participant count alone cannot
reproduce the real final allocation — never hiding that limitation
behind a plausible-looking single value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .offering_terms import OfferingTerms

AllocationScenarioStatus = Literal["computed", "unavailable"]

_DEMAND_DISTRIBUTION_CAVEAT = (
    "hypothetical_retail_participant_count alone cannot reproduce the actual final per-investor allocation: "
    "real equal-distribution mechanics typically satisfy smaller/fully-satisfiable orders first and redistribute "
    "the remaining shares among the rest, which this scenario calculator does not model. The figures below are a "
    "deterministic floor/remainder baseline at the modeled participant count, not a prediction of the real outcome."
)


@dataclass(frozen=True, slots=True)
class AllocationScenario:
    """One hypothetical-demand scenario for the retail tranche.

    All numeric fields are only populated when ``status == "computed"``
    — i.e. ``retail_distribution_rule`` is confirmed ``"equal"`` and
    ``retail_offered_shares`` is resolved (``offer_price`` additionally
    gates the two TL fields).

    - ``average_shares_per_participant``: the plain
      ``retail_offered_shares / hypothetical_retail_participant_count``
      ratio — informational only, not a claim any investor actually
      receives this (usually fractional) amount.
    - ``base_integer_allocation``: ``floor(retail_offered_shares /
      hypothetical_retail_participant_count)`` — the whole-share amount
      every participant can receive at minimum under equal distribution,
      *before* accounting for ``remainder_shares``.
    - ``remainder_shares``: the whole shares left over after every
      participant receives ``base_integer_allocation`` — this many
      participants (out of the hypothetical total) would receive one
      extra share *if* leftover shares were distributed by this
      calculator's own simple modeling assumption alone; the real
      mechanism (see module docstring) can differ.
    - ``allocation_range_shares``: ``(base_integer_allocation,
      base_integer_allocation)`` when ``remainder_shares == 0``, else
      ``(base_integer_allocation, base_integer_allocation + 1)`` — the
      baseline range a given hypothetical participant's share count
      would fall in under this calculator's own modeling assumption.
    - ``tl_allocation_baseline``/``tl_allocation_range``: the same two
      figures priced at ``offer_price`` — the TL-equivalent baseline and
      range (also the practical minimum order size implied by the
      baseline, under this calculator's up-front-order-capital
      assumption — see ``assumptions``).
    """

    hypothetical_retail_participant_count: int
    status: AllocationScenarioStatus

    average_shares_per_participant: float | None
    base_integer_allocation: int | None
    remainder_shares: int | None
    allocation_range_shares: tuple[int, int] | None

    tl_allocation_baseline: float | None
    tl_allocation_range: tuple[float, float] | None

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
            average_shares_per_participant=None,
            base_integer_allocation=None,
            remainder_shares=None,
            allocation_range_shares=None,
            tl_allocation_baseline=None,
            tl_allocation_range=None,
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

    can_compute = rule.status == "extracted" and rule.value == "equal" and retail_shares.status == "extracted"

    if offer_price.status != "extracted":
        caveats.append(f"offer_price is {offer_price.status} — cannot compute a TL allocation value without it.")

    average_shares_per_participant: float | None = None
    base_integer_allocation: int | None = None
    remainder_shares: int | None = None
    allocation_range_shares: tuple[int, int] | None = None
    tl_allocation_baseline: float | None = None
    tl_allocation_range: tuple[float, float] | None = None
    assumptions: list[str] = []

    if can_compute:
        total_shares = retail_shares.value
        average_shares_per_participant = total_shares / hypothetical_retail_participant_count
        base_integer_allocation = int(total_shares // hypothetical_retail_participant_count)
        remainder_shares = round(total_shares - base_integer_allocation * hypothetical_retail_participant_count)
        allocation_range_shares = (
            (base_integer_allocation, base_integer_allocation)
            if remainder_shares == 0
            else (base_integer_allocation, base_integer_allocation + 1)
        )

        assumptions.append(
            "equal distribution baseline: retail_offered_shares divided by the hypothetical participant count via "
            "floor/remainder arithmetic (base_integer_allocation/remainder_shares), not the real order-satisfaction "
            "and redistribution mechanics — see caveats."
        )
        assumptions.append(
            "'share' here is treated as the allocation unit directly (1 lot = 1 share at nominal/par value) — this "
            "project extracts no separate lot-size field; every real sampled document that defines a lot ties it "
            "1:1 to a single share, but that definition is not independently re-verified per company here."
        )
        assumptions.append(
            "assumes each hypothetical investor applies independently for exactly the modeled allocation amount "
            "(the practical minimum order needed to receive it under equal distribution); it does not model "
            "whether the offering collects the order amount in full up front or against a credit line."
        )

        if offer_price.status == "extracted":
            tl_allocation_baseline = base_integer_allocation * offer_price.value
            tl_allocation_range = (
                (tl_allocation_baseline, tl_allocation_baseline)
                if remainder_shares == 0
                else (tl_allocation_baseline, (base_integer_allocation + 1) * offer_price.value)
            )

        caveats.append(_DEMAND_DISTRIBUTION_CAVEAT)

    status: AllocationScenarioStatus = "computed" if can_compute else "unavailable"
    return AllocationScenario(
        hypothetical_retail_participant_count=hypothetical_retail_participant_count,
        status=status,
        average_shares_per_participant=average_shares_per_participant,
        base_integer_allocation=base_integer_allocation,
        remainder_shares=remainder_shares,
        allocation_range_shares=allocation_range_shares,
        tl_allocation_baseline=tl_allocation_baseline,
        tl_allocation_range=tl_allocation_range,
        assumptions=tuple(assumptions),
        caveats=tuple(caveats),
    )


def allocation_scenario_as_dict(scenario: AllocationScenario) -> dict:
    return {
        "hypothetical_retail_participant_count": scenario.hypothetical_retail_participant_count,
        "status": scenario.status,
        "average_shares_per_participant": scenario.average_shares_per_participant,
        "base_integer_allocation": scenario.base_integer_allocation,
        "remainder_shares": scenario.remainder_shares,
        "allocation_range_shares": list(scenario.allocation_range_shares) if scenario.allocation_range_shares else None,
        "tl_allocation_baseline": scenario.tl_allocation_baseline,
        "tl_allocation_range": list(scenario.tl_allocation_range) if scenario.tl_allocation_range else None,
        "assumptions": list(scenario.assumptions),
        "caveats": list(scenario.caveats),
    }


__all__ = [
    "AllocationScenario",
    "AllocationScenarioStatus",
    "allocation_scenario_as_dict",
    "build_allocation_scenario",
]
