"""Connects a hypothetical retail :class:`~halka_arz_advisor.kap.allocation_scenario.AllocationScenario`'s
TL capital exposure to plausible TL profit/loss outcomes.

:mod:`halka_arz_advisor.decision.subscription_v1` decides *whether* to
subscribe; this module answers a different, narrower question it
deliberately leaves open — "if I did, roughly how much money is
actually at stake, and what could I plausibly gain or lose in TL?" —
by composing two pieces of evidence that already exist elsewhere,
without touching either:

- :mod:`halka_arz_advisor.kap.allocation_scenario` for how many shares
  (and how much TL) a hypothetical demand scenario implies.
- :mod:`halka_arz_advisor.ipo_outcomes.regime` for how *other*,
  already-settled recent IPOs actually performed — via
  :func:`~halka_arz_advisor.ipo_outcomes.regime.select_mature_outcomes`,
  the exact same leakage-safe, point-in-time selection
  ``decision.subscription_v1``'s own ``RecentIpoRegime`` read uses, so
  this module can never see more (or different) evidence than that one
  already treats as safe.

No new extraction, no new participant-count forecasting (a demand
scenario's hypothetical participant count is always the caller's
what-if input, exactly as in ``kap.allocation_scenario`` itself — never
predicted here), and no new expected-return model. Two independent
kinds of uncertainty are kept explicit and are never collapsed into one
number:

- **Allocation uncertainty** — how many shares (and how much capital) a
  given demand scenario would actually get you. Already modeled by
  ``AllocationScenario`` itself as a baseline plus a floor/remainder
  range, not a single confident number; this module passes that
  range through unchanged rather than picking a point estimate.
- **Return uncertainty** — what the stock might actually do after
  listing. This module never invents an expected return. When at
  least :data:`~halka_arz_advisor.ipo_outcomes.regime.STRONG_EVIDENCE_MATURE_IPO_COUNT`
  other, already-settled recent IPOs have a resolved 5-trading-day
  return, the return scenarios are the worst/median/best *actual*
  returns among them — real historical outcomes, not a fitted
  distribution or a forecast of this IPO's own return. Below that
  evidence bar, return scenarios fall back to a small, fixed,
  clearly-labeled illustrative band (:data:`ILLUSTRATIVE_RETURN_SCENARIOS`)
  that is never presented as a prediction.

Equal-distribution allocation mechanics are not, and must never be
read as, an investment edge: this module attaches no verdict, edge, or
recommendation of its own — it is pure downstream arithmetic over
figures ``decision.subscription_v1`` (edge/mechanics) already keeps
separate, and never feeds back into that module's own action/edge
logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Literal

from ..ipo_outcomes.models import IpoMarketOutcome
from ..ipo_outcomes.regime import DEFAULT_LOOKBACK_DAYS, STRONG_EVIDENCE_MATURE_IPO_COUNT, select_mature_outcomes
from ..kap.allocation_scenario import AllocationScenario

SUBSCRIPTION_ECONOMICS_VERSION = "subscription_economics_r1"

ReturnScenarioSource = Literal["historical_regime", "illustrative"]

# A small, fixed, stated illustrative band — never fit against this
# project's own ipo_outcomes data, and only ever used when there isn't
# a defensible historical sample (see build_return_scenarios). A
# symmetric +/-10% band plus one larger upside case, not a forecast of
# any kind — every label says "gösterge" (illustrative/indicative).
ILLUSTRATIVE_RETURN_SCENARIOS: tuple[tuple[str, float], ...] = (
    ("Kötü senaryo (gösterge)", -0.10),
    ("İyi senaryo (gösterge)", 0.10),
    ("Çok iyi senaryo (gösterge)", 0.25),
)

# Labels for kap.allocation_scenario.DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS'
# ascending-participant-count ordering (fewer hypothetical participants
# -> more shares per participant -> "low demand"). Positional, not
# keyed by the exact counts, so a future change to those counts can't
# silently mislabel a scenario.
DEMAND_LABELS_ASCENDING: tuple[str, ...] = ("Düşük talep", "Tipik talep", "Yüksek talep")


@dataclass(frozen=True, slots=True)
class ReturnScenario:
    """One plausible post-listing return, as a fraction (``0.10`` ==
    +10%) — never a probability-weighted expectation."""

    label: str
    return_pct: float
    source: ReturnScenarioSource
    basis: str


@dataclass(frozen=True, slots=True)
class ReturnOutcome:
    scenario: ReturnScenario
    profit_loss_tl: float


@dataclass(frozen=True, slots=True)
class AllocationEconomics:
    """One demand scenario's shares/capital (from ``AllocationScenario``,
    unchanged) plus what each return scenario would mean in TL on that
    scenario's own baseline capital. Empty ``return_outcomes`` when the
    underlying ``AllocationScenario`` itself couldn't resolve a TL
    baseline (see its own ``status``/``caveats``) — never a guessed
    capital figure."""

    demand_label: str
    allocation_scenario: AllocationScenario
    capital_tl: float | None
    capital_tl_range: tuple[float, float] | None
    return_outcomes: tuple[ReturnOutcome, ...]


@dataclass(frozen=True, slots=True)
class PersonalCapitalContext:
    """The simplest possible personal-capital input: one investor-level
    TL figure, not a portfolio — deliberately not a portfolio-management
    subsystem (position sizing, multi-IPO aggregation, risk budgets are
    all out of scope here)."""

    available_capital_tl: float


@dataclass(frozen=True, slots=True)
class SubscriptionEconomics:
    return_scenario_source: ReturnScenarioSource
    return_scenario_basis: str
    allocations: tuple[AllocationEconomics, ...]
    personal_capital_notes: tuple[str, ...]
    version: str = SUBSCRIPTION_ECONOMICS_VERSION


def build_return_scenarios(
    recent_ipo_outcomes: Sequence[IpoMarketOutcome],
    *,
    as_of: datetime,
    exclude_ticker: str | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[tuple[ReturnScenario, ...], ReturnScenarioSource]:
    """Pure — no I/O, no randomness. Returns ``(scenarios, source)``.

    ``source`` is ``"historical_regime"`` only when at least
    :data:`STRONG_EVIDENCE_MATURE_IPO_COUNT` other, already-settled
    recent IPOs (the same leakage-safe selection
    :func:`~halka_arz_advisor.ipo_outcomes.regime.build_recent_ipo_regime`
    uses) have a resolved *raw* (not BIST-relative) 5-day return — the
    figure that prices what an investor would have actually realized in
    TL, unlike the BIST-relative figure ``RecentIpoRegime`` itself reads
    for its own, different purpose (classifying a cross-sectional
    regime, not pricing a P&L). ``STRONG_EVIDENCE_MATURE_IPO_COUNT`` is
    reused rather than a new threshold invented for this module — it is
    this project's own existing bar for "a clearly stronger evidence
    base", and a worst/median/best read needs at least that many real
    data points to be more than noise."""
    mature = select_mature_outcomes(recent_ipo_outcomes, as_of=as_of, exclude_ticker=exclude_ticker, lookback_days=lookback_days)
    returns = sorted(o.return_5d for o in mature if o.return_5d is not None)

    if len(returns) < STRONG_EVIDENCE_MATURE_IPO_COUNT:
        basis = (
            f"only {len(returns)} other, already-settled recent IPO(s) with a resolved 5-day return in the last "
            f"{lookback_days} day(s) — fewer than the {STRONG_EVIDENCE_MATURE_IPO_COUNT} this project treats as a "
            "defensible sample, so these are fixed illustrative scenarios, not a forecast."
        )
        return (
            tuple(
                ReturnScenario(label=label, return_pct=pct, source="illustrative", basis=basis)
                for label, pct in ILLUSTRATIVE_RETURN_SCENARIOS
            ),
            "illustrative",
        )

    worst, typical, best = returns[0], median(returns), returns[-1]
    basis = (
        f"actual 5-trading-day returns of {len(returns)} other, already-settled recent IPOs in the last "
        f"{lookback_days} day(s) — real historical outcomes, not a prediction of this IPO's own return."
    )
    scenarios = (
        ReturnScenario(label="Kötü senaryo (yakın dönem en düşüğü)", return_pct=worst / 100.0, source="historical_regime", basis=basis),
        ReturnScenario(label="Tipik senaryo (yakın dönem medyanı)", return_pct=typical / 100.0, source="historical_regime", basis=basis),
        ReturnScenario(label="İyi senaryo (yakın dönem en yükseği)", return_pct=best / 100.0, source="historical_regime", basis=basis),
    )
    return scenarios, "historical_regime"


def build_allocation_economics(
    allocation_scenario: AllocationScenario,
    demand_label: str,
    return_scenarios: Sequence[ReturnScenario],
) -> AllocationEconomics:
    """Pure. ``return_outcomes`` is empty when ``allocation_scenario``
    itself has no resolved TL baseline — never a guessed capital
    figure (see ``AllocationScenario.tl_allocation_baseline``)."""
    capital = allocation_scenario.tl_allocation_baseline
    if capital is None:
        return AllocationEconomics(
            demand_label=demand_label,
            allocation_scenario=allocation_scenario,
            capital_tl=None,
            capital_tl_range=None,
            return_outcomes=(),
        )
    outcomes = tuple(ReturnOutcome(scenario=rs, profit_loss_tl=capital * rs.return_pct) for rs in return_scenarios)
    return AllocationEconomics(
        demand_label=demand_label,
        allocation_scenario=allocation_scenario,
        capital_tl=capital,
        capital_tl_range=allocation_scenario.tl_allocation_range,
        return_outcomes=outcomes,
    )


def _personal_capital_notes(allocations: Sequence[AllocationEconomics], personal_capital: PersonalCapitalContext) -> tuple[str, ...]:
    available = personal_capital.available_capital_tl
    notes: list[str] = []
    for allocation in allocations:
        if allocation.capital_tl is None:
            continue
        if allocation.capital_tl > available:
            notes.append(
                f"{allocation.demand_label}: gereken sermaye (~{allocation.capital_tl:,.0f} TL) belirttiğiniz "
                f"{available:,.0f} TL'yi aşıyor — bu senaryoda tahsisatın tamamını karşılayamayabilirsiniz.".replace(",", ".")
            )
        else:
            share_pct = (allocation.capital_tl / available) * 100.0 if available > 0 else None
            share_str = f"yaklaşık %{share_pct:.1f}'i" if share_pct is not None else "bilinmiyor"
            notes.append(
                f"{allocation.demand_label}: gereken sermaye belirttiğiniz sermayenin {share_str} "
                f"(~{allocation.capital_tl:,.0f} TL / {available:,.0f} TL).".replace(",", ".")
            )
    return tuple(notes)


def build_subscription_economics(
    allocation_scenarios: Sequence[AllocationScenario],
    *,
    recent_ipo_outcomes: Sequence[IpoMarketOutcome],
    as_of: datetime,
    exclude_ticker: str | None,
    personal_capital: PersonalCapitalContext | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> SubscriptionEconomics:
    """Pure — no I/O. ``allocation_scenarios`` is expected to be exactly
    what ``decision.subscription_v1`` already built (its own
    ``DEFAULT_ALLOCATION_SCENARIO_PARTICIPANT_COUNTS`` scenarios); this
    function never builds its own."""
    return_scenarios, source = build_return_scenarios(
        recent_ipo_outcomes, as_of=as_of, exclude_ticker=exclude_ticker, lookback_days=lookback_days
    )
    basis = return_scenarios[0].basis if return_scenarios else ""

    if len(allocation_scenarios) == len(DEMAND_LABELS_ASCENDING):
        labels: Sequence[str] = DEMAND_LABELS_ASCENDING
    else:
        labels = tuple(
            f"Senaryo {i + 1} ({scenario.hypothetical_retail_participant_count:,} katılımcı varsayımı)".replace(",", ".")
            for i, scenario in enumerate(allocation_scenarios)
        )

    allocations = tuple(
        build_allocation_economics(scenario, label, return_scenarios) for scenario, label in zip(allocation_scenarios, labels)
    )

    personal_notes = _personal_capital_notes(allocations, personal_capital) if personal_capital is not None else ()

    return SubscriptionEconomics(
        return_scenario_source=source,
        return_scenario_basis=basis,
        allocations=allocations,
        personal_capital_notes=personal_notes,
    )


def subscription_economics_as_dict(economics: SubscriptionEconomics) -> dict:
    from ..kap.allocation_scenario import allocation_scenario_as_dict

    return {
        "return_scenario_source": economics.return_scenario_source,
        "return_scenario_basis": economics.return_scenario_basis,
        "allocations": [
            {
                "demand_label": allocation.demand_label,
                "allocation_scenario": allocation_scenario_as_dict(allocation.allocation_scenario),
                "capital_tl": allocation.capital_tl,
                "capital_tl_range": list(allocation.capital_tl_range) if allocation.capital_tl_range else None,
                "return_outcomes": [
                    {
                        "label": outcome.scenario.label,
                        "return_pct": outcome.scenario.return_pct,
                        "source": outcome.scenario.source,
                        "profit_loss_tl": outcome.profit_loss_tl,
                    }
                    for outcome in allocation.return_outcomes
                ],
            }
            for allocation in economics.allocations
        ],
        "personal_capital_notes": list(economics.personal_capital_notes),
        "version": economics.version,
    }


__all__ = [
    "DEMAND_LABELS_ASCENDING",
    "ILLUSTRATIVE_RETURN_SCENARIOS",
    "SUBSCRIPTION_ECONOMICS_VERSION",
    "AllocationEconomics",
    "PersonalCapitalContext",
    "ReturnOutcome",
    "ReturnScenario",
    "ReturnScenarioSource",
    "SubscriptionEconomics",
    "build_allocation_economics",
    "build_return_scenarios",
    "build_subscription_economics",
    "subscription_economics_as_dict",
]
