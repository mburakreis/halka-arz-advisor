"""Smallest economically meaningful pre-offer valuation sanity layer:
implied post-money market cap **at the actual offer price**, and the
handful of multiples (P/E, P/S, P/B, EV/EBITDA) that can be derived
from it plus already-extracted financial-statement facts — reused
unmodified, no new extraction, no duplicate parsing logic.

**A distinct anchor from :mod:`halka_arz_advisor.kap.derived_financials`'s**
``recalculated_pe``. That module anchors on
:class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`'s
``reported_post_money_market_cap`` — the price-determination-report's
own *proposed* valuation figure (almost always stated in USD millions,
see that module's own docstring), which can predate and differ from the
actual final offer price a subscriber pays. This module instead anchors
on :attr:`~halka_arz_advisor.kap.offering_terms.OfferingTerms.implied_post_money_market_cap`
(``offer_price × post_offer_share_count``, always raw TRY) — the real,
final pre-offer economic term this project already computes in
``kap.offering_terms``. Both anchors are legitimate and answer different
questions; this one is the canonical valuation evidence
:mod:`halka_arz_advisor.decision.subscription_v1` reads for its
``ownership_view`` valuation-anchor gate (never both, never a
duplicate calculation — see that module).

**Scale conversion, and why this differs from ``derived_financials``'s
"never normalize" rule.** ``derived_financials`` deliberately requires
two :class:`~halka_arz_advisor.kap.financials.FinancialObservation`\\ s
to share the *exact same* ``scale`` before comparing them — conservative,
since either side's true magnitude relative to the other could be
ambiguous. Here, one side (``implied_post_money_market_cap``) is always
exactly known in raw TRY (an arithmetic product of two already-verified
numbers, never itself scaled) — converting the *other* side's own
explicitly-parsed, never-guessed ``scale`` marker ("unit"/"thousand"/
"million", see ``kap.financials``'s own deterministic scale parser) into
raw units before dividing is plain unit arithmetic (kilometers to
meters), not an accounting-compatibility assumption. Currency and period
compatibility are still required *exactly*, never converted or assumed
— an observation in a currency other than TRY, or with an unrecognized
scale marker, is simply not used.

**Never a cheap/expensive verdict.** This module has no comparison
basis (no peer multiples, no sector medians, no historical-outcome
data) to judge a multiple against, and building one is explicitly out
of scope for this pass — see :func:`build_valuation_evidence`'s
``sufficiency``, which only ever states whether *enough evidence
exists* for a human to do their own price sanity check, never whether
the price itself is cheap or expensive. A headline "discount" figure
from a price determination report is not read by this module at all.

**Leakage safety.** :class:`~halka_arz_advisor.kap.financials.FinancialObservation`
is currently only ever populated from ``price_determination_report``
documents (a pre-offer document, filed to justify/support the offer
price before subscription opens — see ``kap.documents``'s own
eligible-document-type scoping) — but this module re-filters to that
document type explicitly anyway, rather than trusting the caller's
selection, the same defensive pattern
:func:`halka_arz_advisor.kap.offering_terms._pre_offer_safe_fact` uses
and for the same confirmed-real reason: a shared field/data slot can
end up populated from an unexpected document type in the future, and a
valuation sanity layer that will genuinely be used with real money
should not depend on every future caller remembering to filter
correctly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from .financials import FinancialObservation
from .manual_confirmation import CompletedOfferingTerms, effective_offering_terms
from .offering_terms import OfferingTerms
from .sector import Sector

FORMULA_VERSION = "1"

ValuationFeatureStatus = Literal["computed", "unavailable", "not_applicable"]
ValuationSufficiency = Literal["SUFFICIENT", "INSUFFICIENT"]

VALUATION_FEATURE_NAMES: tuple[str, ...] = (
    "implied_market_cap",
    "pe_at_offer",
    "ps_at_offer",
    "pb_at_offer",
    "ev_ebitda_at_offer",
)

# The only document type FinancialObservation is currently populated
# from (see module docstring) — re-filtered here explicitly rather than
# trusted, the same defensive pattern kap.offering_terms already uses.
_PRE_OFFER_SAFE_FINANCIAL_DOCUMENT_TYPES = frozenset({"price_determination_report"})

# kap.financials's own deterministic scale parser only ever produces
# one of these three (see that module's _parse_currency_and_scale) —
# never guessed here either; an observation with any other value is
# simply not used.
_SCALE_MULTIPLIERS: dict[str, float] = {"unit": 1.0, "thousand": 1_000.0, "million": 1_000_000.0}

# P/S needs a "revenue" concept — not meaningful for an insurer, which
# reports written premiums instead (see kap.financials's own docstring
# and kap.sector.SECTOR_INAPPLICABLE_DERIVED_FEATURES's identical
# net_margin/revenue_growth_yoy override for the same reason). P/E and
# P/B remain broadly applicable across every sector this project
# classifies.
_SECTOR_INAPPLICABLE_VALUATION_FEATURES: dict[Sector, frozenset[str]] = {
    "insurance": frozenset({"ps_at_offer"}),
}


@dataclass(frozen=True, slots=True)
class ValuationFeature:
    """One valuation fact's result — a computed ``value`` with the
    exact period it's anchored to and the observation(s) it came from,
    or ``"unavailable"``/``"not_applicable"`` with an explicit reason.
    Never a neutral/placeholder value, and never a judgment of whether
    ``value`` is cheap or expensive."""

    feature_name: str
    status: ValuationFeatureStatus
    value: float | None
    unit: str | None  # "TRY" for implied_market_cap; None (a ratio) for every multiple
    period_end: date | None
    unavailable_reason: str | None
    input_observation_ids: tuple[str, ...]
    formula_version: str


@dataclass(frozen=True, slots=True)
class ValuationEvidence:
    """One company's full pre-offer valuation sanity check —
    :attr:`sufficiency` states only whether *enough evidence exists*
    for a human to do their own price sanity check, never whether the
    price itself is cheap or expensive (see module docstring)."""

    implied_market_cap: ValuationFeature
    pe_at_offer: ValuationFeature
    ps_at_offer: ValuationFeature
    pb_at_offer: ValuationFeature
    ev_ebitda_at_offer: ValuationFeature
    sufficiency: ValuationSufficiency
    sufficiency_reason: str
    formula_version: str

    def as_dict(self) -> dict[str, ValuationFeature]:
        return {name: getattr(self, name) for name in VALUATION_FEATURE_NAMES}


def _observation_id(obs: FinancialObservation) -> str:
    start = obs.period_start.isoformat() if obs.period_start else "-"
    end = obs.period_end.isoformat() if obs.period_end else "-"
    return f"{obs.source.document_type}:{obs.source.disclosure_id}:p{obs.source.page_number}:{obs.metric_name}:{start}:{end}"


def _unavailable(feature_name: str, period_end: date | None, reason: str, *, input_observation_ids: tuple[str, ...] = ()) -> ValuationFeature:
    return ValuationFeature(
        feature_name=feature_name, status="unavailable", value=None, unit=None, period_end=period_end,
        unavailable_reason=reason, input_observation_ids=input_observation_ids, formula_version=FORMULA_VERSION,
    )


def _not_applicable(feature_name: str, reason: str) -> ValuationFeature:
    return ValuationFeature(
        feature_name=feature_name, status="not_applicable", value=None, unit=None, period_end=None,
        unavailable_reason=reason, input_observation_ids=(), formula_version=FORMULA_VERSION,
    )


def _computed(feature_name: str, value: float, period_end: date | None, *, input_observation_ids: tuple[str, ...]) -> ValuationFeature:
    return ValuationFeature(
        feature_name=feature_name, status="computed", value=value, unit=None, period_end=period_end,
        unavailable_reason=None, input_observation_ids=input_observation_ids, formula_version=FORMULA_VERSION,
    )


def _to_raw_units(obs: FinancialObservation) -> float | None:
    multiplier = _SCALE_MULTIPLIERS.get(obs.scale)
    return None if multiplier is None else obs.value * multiplier


def _latest_annual_try(metric_name: str, observations: Sequence[FinancialObservation]) -> FinancialObservation | None:
    """The most recent ANNUAL, TRY-denominated observation for
    ``metric_name`` — ANNUAL for every one of the three ratios below
    (including the balance-sheet-derived P/B) so a full fiscal-year
    income/revenue figure is never paired against an interim (e.g.
    quarter-end) balance sheet, or vice versa."""
    candidates = [o for o in observations if o.metric_name == metric_name and o.period_type == "ANNUAL" and o.currency == "TRY"]
    if not candidates:
        return None
    return max(candidates, key=lambda o: o.period_end)


def _market_cap_feature(offering_terms: OfferingTerms, completed_terms: CompletedOfferingTerms) -> ValuationFeature:
    effective = effective_offering_terms(offering_terms, completed_terms)
    field = effective.implied_post_money_market_cap
    if field.status != "extracted":
        return _unavailable("implied_market_cap", None, f"implied_post_money_market_cap is {field.status}")
    return ValuationFeature(
        feature_name="implied_market_cap", status="computed", value=field.value, unit="TRY", period_end=None,
        unavailable_reason=None, input_observation_ids=(), formula_version=FORMULA_VERSION,
    )


def _ratio_at_offer(
    feature_name: str, market_cap: ValuationFeature, metric_name: str, observations: Sequence[FinancialObservation],
    *, positive_denominator_required: bool, denominator_label: str,
) -> ValuationFeature:
    if market_cap.status != "computed":
        return _unavailable(feature_name, None, f"implied_market_cap is {market_cap.status}")

    denominator_obs = _latest_annual_try(metric_name, observations)
    if denominator_obs is None:
        return _unavailable(feature_name, None, f"no ANNUAL {metric_name} observation in TRY is available")

    raw_value = _to_raw_units(denominator_obs)
    ids = (_observation_id(denominator_obs),)
    if raw_value is None:
        return _unavailable(
            feature_name, denominator_obs.period_end,
            f"{metric_name}'s reported scale {denominator_obs.scale!r} is not a recognized unit/thousand/million scale",
            input_observation_ids=ids,
        )
    if positive_denominator_required and raw_value <= 0:
        return _unavailable(
            feature_name, denominator_obs.period_end,
            f"latest ANNUAL {denominator_label} is zero or negative — {feature_name} is not meaningful",
            input_observation_ids=ids,
        )
    if raw_value == 0:
        return _unavailable(feature_name, denominator_obs.period_end, f"latest ANNUAL {denominator_label} is zero", input_observation_ids=ids)

    value = market_cap.value / raw_value  # type: ignore[operator]
    return _computed(feature_name, value, denominator_obs.period_end, input_observation_ids=ids)


def compute_pe_at_offer(market_cap: ValuationFeature, observations: Sequence[FinancialObservation]) -> ValuationFeature:
    return _ratio_at_offer(
        "pe_at_offer", market_cap, "net_income", observations, positive_denominator_required=True, denominator_label="net income",
    )


def compute_ps_at_offer(market_cap: ValuationFeature, observations: Sequence[FinancialObservation]) -> ValuationFeature:
    return _ratio_at_offer(
        "ps_at_offer", market_cap, "revenue", observations, positive_denominator_required=True, denominator_label="revenue",
    )


def compute_pb_at_offer(market_cap: ValuationFeature, observations: Sequence[FinancialObservation]) -> ValuationFeature:
    return _ratio_at_offer(
        "pb_at_offer", market_cap, "equity", observations, positive_denominator_required=True, denominator_label="book equity",
    )


def compute_ev_ebitda_at_offer() -> ValuationFeature:
    """Always ``"unavailable"`` today: this project extracts no
    depreciation/amortization metric (see
    :data:`halka_arz_advisor.kap.financials.FINANCIAL_METRIC_NAMES`),
    and ``operating_profit`` (EBIT, when a report states it under that
    exact label) is not EBITDA on its own — approximating one from the
    other would be fabricating a multiple from an incompatible input,
    exactly what this module exists not to do. Kept as an explicit,
    honest function (not simply omitted) so a future D&A extractor only
    needs to fill this in, not redesign the evidence shape."""
    return _unavailable(
        "ev_ebitda_at_offer", None,
        "EBITDA cannot be derived: no depreciation/amortization metric is extracted, and operating_profit "
        "(EBIT) alone is not EBITDA",
    )


def build_valuation_evidence(
    offering_terms: OfferingTerms,
    completed_terms: CompletedOfferingTerms,
    observations: Sequence[FinancialObservation],
    sector: Sector = "unknown",
) -> ValuationEvidence:
    """Pure — no I/O. ``observations`` should be whatever
    :class:`~halka_arz_advisor.kap.financials.FinancialObservation`\\ s
    the caller already aggregated for this company (see
    :func:`halka_arz_advisor.kap.documents.aggregate_company_financial_series`)
    — re-filtered here to pre-offer-safe document types regardless (see
    module docstring)."""
    safe_observations = tuple(
        obs for obs in observations if obs.source.document_type in _PRE_OFFER_SAFE_FINANCIAL_DOCUMENT_TYPES
    )

    market_cap = _market_cap_feature(offering_terms, completed_terms)
    pe = compute_pe_at_offer(market_cap, safe_observations)
    ps = compute_ps_at_offer(market_cap, safe_observations)
    pb = compute_pb_at_offer(market_cap, safe_observations)
    ev_ebitda = compute_ev_ebitda_at_offer()

    inapplicable = _SECTOR_INAPPLICABLE_VALUATION_FEATURES.get(sector, frozenset())
    if "ps_at_offer" in inapplicable:
        ps = _not_applicable("ps_at_offer", f"not a meaningful concept for sector={sector!r}")

    computed_multiples = [f for f in (pe, ps, pb) if f.status == "computed"]
    if market_cap.status != "computed":
        sufficiency: ValuationSufficiency = "INSUFFICIENT"
        reason = f"no valuation anchor at all: implied_market_cap is {market_cap.status} ({market_cap.unavailable_reason})"
    elif computed_multiples:
        sufficiency = "SUFFICIENT"
        names = ", ".join(f.feature_name for f in computed_multiples)
        reason = f"implied market cap resolved and {len(computed_multiples)} multiple(s) computed from compatible pre-offer-safe evidence: {names}"
    else:
        sufficiency = "INSUFFICIENT"
        reason = "implied market cap is known, but no compatible P/E, P/S, or P/B multiple could be computed from currently resolved financial-statement evidence"

    return ValuationEvidence(
        implied_market_cap=market_cap, pe_at_offer=pe, ps_at_offer=ps, pb_at_offer=pb, ev_ebitda_at_offer=ev_ebitda,
        sufficiency=sufficiency, sufficiency_reason=reason, formula_version=FORMULA_VERSION,
    )


def valuation_feature_as_dict(feature: ValuationFeature) -> dict:
    return {
        "feature_name": feature.feature_name,
        "status": feature.status,
        "value": feature.value,
        "unit": feature.unit,
        "period_end": feature.period_end.isoformat() if feature.period_end else None,
        "unavailable_reason": feature.unavailable_reason,
        "input_observation_ids": list(feature.input_observation_ids),
        "formula_version": feature.formula_version,
    }


def valuation_evidence_as_dict(evidence: ValuationEvidence) -> dict:
    return {
        **{name: valuation_feature_as_dict(getattr(evidence, name)) for name in VALUATION_FEATURE_NAMES},
        "sufficiency": evidence.sufficiency,
        "sufficiency_reason": evidence.sufficiency_reason,
        "formula_version": evidence.formula_version,
    }


__all__ = [
    "FORMULA_VERSION",
    "VALUATION_FEATURE_NAMES",
    "ValuationEvidence",
    "ValuationFeature",
    "ValuationFeatureStatus",
    "ValuationSufficiency",
    "build_valuation_evidence",
    "compute_ev_ebitda_at_offer",
    "compute_pb_at_offer",
    "compute_pe_at_offer",
    "compute_ps_at_offer",
    "valuation_evidence_as_dict",
    "valuation_feature_as_dict",
]
