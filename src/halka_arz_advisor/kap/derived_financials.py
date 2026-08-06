"""First deterministic *derived* financial features, computed on top of
:class:`~halka_arz_advisor.kap.financials.FinancialObservation` (revenue,
net_income) and the explicit valuation fields already extracted from the
price determination report
(:class:`~halka_arz_advisor.kap.extraction.ExtractedFacts`).

This module owns the *formula* — nothing here re-reads a PDF or matches
a regex; it only combines values :mod:`halka_arz_advisor.kap.financials`/
:mod:`halka_arz_advisor.kap.extraction` already produced. Every result
is either ``"computed"`` (with a value and the exact inputs it came
from) or ``"unavailable"`` (with an explicit reason) — never a
0/None/neutral placeholder standing in for "couldn't compute this".

Strict comparison rules (per the brief):

- ``revenue_growth_yoy`` only compares two ``ANNUAL`` revenue
  observations whose consolidation scope, currency, scale, *and*
  inflation-adjustment status all match, and whose years are exactly
  one apart — never an interim period, never annualized, never a
  larger gap silently treated as "the closest available year".
- ``net_margin`` only divides a revenue/net_income pair that share the
  *exact same* period (``period_start`` and ``period_end``), scope,
  currency, and scale — even though the two metrics almost always come
  from the very same table (so this is usually trivially true), it's
  still checked explicitly rather than assumed.
- ``recalculated_pe`` divides ``reported_post_money_market_cap`` (an
  :class:`~halka_arz_advisor.kap.extraction.ExtractedFact` with no
  currency/scale of its own — see :data:`_MARKET_CAP_ASSUMED_CURRENCY`)
  by the latest ``ANNUAL`` net_income observation. Every real report
  inspected while building this project's price-determination-report
  extractor states this field in **millions of USD**
  ("236,2 mn $"/"Nihai Değer") — never TL — so a net_income observation
  is only treated as unit-compatible with it when that observation's
  own ``currency``/``scale`` are ``"USD"``/``"million"`` too. Since the
  income-statement table this project reads net_income from is
  overwhelmingly TL-denominated in real reports (see
  :mod:`halka_arz_advisor.kap.financials`'s own docstring), this
  correctly returns "unavailable" far more often than not today — an
  honest reflection of a real cross-currency gap in the underlying
  data, not a bug. Blindly dividing mismatched-currency numbers would
  silently produce a meaningless ratio (confirmed empirically against a
  real report before writing this gate: dividing a USD-millions market
  cap by a raw-TL net income produces a value several orders of
  magnitude off any real P/E).
- ``reported_pe_difference_percentage`` compares ``recalculated_pe``
  against the report's own explicit ``reported_pe`` fact — unavailable
  whenever either side is.
- ``net_debt`` = ``financial_debt`` - ``cash_and_equivalents``,
  ``debt_to_equity`` = ``financial_debt`` / ``equity``, ``current_ratio``
  = ``current_assets`` / ``current_liabilities``,
  ``operating_cash_flow_to_net_income`` = ``operating_cash_flow`` /
  ``net_income``, and ``interest_coverage`` = ``operating_profit`` /
  ``finance_expense`` all follow the same rule as ``net_margin``: the
  two input observations must share the exact same period, scope,
  currency, and scale — never a different period, never a different
  scope, never a different scale.

A denominator that's zero, or negative where the ratio wouldn't be a
meaningful one (equity, current liabilities, net income, finance
expense — but not net_debt's/debt_to_equity's numerator, since a
company legitimately can hold more cash than debt, or spend more than
it earns), leaves the feature ``"unavailable"`` rather than computed.
None of this annualizes an interim figure, combines consolidated and
standalone observations, or mixes inflation-adjusted and unadjusted
periods.

Sector applicability (see :mod:`halka_arz_advisor.kap.sector`): a
feature that depends on a metric a company's sector never reports in a
comparable sense (e.g. ``net_margin`` for an insurer, which has no
"revenue" concept) is reported ``"not_applicable"`` — a distinct status
from ``"unavailable"``, since the gap here isn't a missing document or
a failed extraction, it's that the concept itself doesn't apply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .extraction import ExtractedFact, ExtractedFacts
from .financials import FinancialObservation
from .sector import Sector, SECTOR_INAPPLICABLE_DERIVED_FEATURES

FORMULA_VERSION = "1"

DerivedFeatureStatus = Literal["computed", "unavailable", "not_applicable"]

DERIVED_FINANCIAL_FEATURE_NAMES: tuple[str, ...] = (
    "revenue_growth_yoy",
    "net_margin",
    "recalculated_pe",
    "reported_pe_difference_percentage",
    "net_debt",
    "debt_to_equity",
    "current_ratio",
    "operating_cash_flow_to_net_income",
    "interest_coverage",
)

# See module docstring — the one currency/scale reported_post_money_market_cap
# has ever been observed stated in, and the only one it's trusted against.
_MARKET_CAP_ASSUMED_CURRENCY = "USD"
_MARKET_CAP_ASSUMED_SCALE = "million"


@dataclass(frozen=True, slots=True)
class DerivedFinancialFeature:
    """One derived feature's result — either a computed ``value`` with
    the exact inputs it came from, or ``"unavailable"`` with an
    explicit reason. Never a neutral/placeholder value."""

    feature_name: str
    status: DerivedFeatureStatus
    value: float | None
    unavailable_reason: str | None
    input_observation_ids: tuple[str, ...]
    source_fact_ids: tuple[str, ...]
    formula_version: str


@dataclass(frozen=True, slots=True)
class DerivedFinancialFeatures:
    revenue_growth_yoy: DerivedFinancialFeature
    net_margin: DerivedFinancialFeature
    recalculated_pe: DerivedFinancialFeature
    reported_pe_difference_percentage: DerivedFinancialFeature
    net_debt: DerivedFinancialFeature
    debt_to_equity: DerivedFinancialFeature
    current_ratio: DerivedFinancialFeature
    operating_cash_flow_to_net_income: DerivedFinancialFeature
    interest_coverage: DerivedFinancialFeature

    def as_dict(self) -> dict[str, DerivedFinancialFeature]:
        return {name: getattr(self, name) for name in DERIVED_FINANCIAL_FEATURE_NAMES}


def _observation_id(obs: FinancialObservation) -> str:
    """A stable, human-readable provenance key — not a database id,
    just a deterministic reference back to exactly which observation
    (document/page/metric/period) a derived value depended on."""
    start = obs.period_start.isoformat() if obs.period_start else "-"
    end = obs.period_end.isoformat() if obs.period_end else "-"
    return f"{obs.source.document_type}:{obs.source.disclosure_id}:p{obs.source.page_number}:{obs.metric_name}:{start}:{end}"


def _fact_id(field_name: str, fact: ExtractedFact) -> str | None:
    if fact.source is None:
        return None
    return f"{fact.source.document_type}:{fact.source.disclosure_id}:p{fact.source.page_number}:{field_name}"


def _unavailable(
    feature_name: str,
    reason: str,
    *,
    input_observation_ids: tuple[str, ...] = (),
    source_fact_ids: tuple[str, ...] = (),
) -> DerivedFinancialFeature:
    return DerivedFinancialFeature(
        feature_name=feature_name,
        status="unavailable",
        value=None,
        unavailable_reason=reason,
        input_observation_ids=input_observation_ids,
        source_fact_ids=source_fact_ids,
        formula_version=FORMULA_VERSION,
    )


def _computed(
    feature_name: str,
    value: float,
    *,
    input_observation_ids: tuple[str, ...] = (),
    source_fact_ids: tuple[str, ...] = (),
) -> DerivedFinancialFeature:
    return DerivedFinancialFeature(
        feature_name=feature_name,
        status="computed",
        value=value,
        unavailable_reason=None,
        input_observation_ids=input_observation_ids,
        source_fact_ids=source_fact_ids,
        formula_version=FORMULA_VERSION,
    )


def _not_applicable(feature_name: str, reason: str) -> DerivedFinancialFeature:
    return DerivedFinancialFeature(
        feature_name=feature_name,
        status="not_applicable",
        value=None,
        unavailable_reason=reason,
        input_observation_ids=(),
        source_fact_ids=(),
        formula_version=FORMULA_VERSION,
    )


def _latest_matching_pair(
    numerator_metric: str, denominator_metric: str, observations: tuple[FinancialObservation, ...]
) -> tuple[FinancialObservation, FinancialObservation] | None:
    """The most recent (highest ``period_end``) pair of observations for
    ``numerator_metric``/``denominator_metric`` sharing the exact same
    period, consolidation scope, currency, scale, and inflation-
    adjustment status — the "net_margin rule" shared by every two-metric
    ratio in this module."""
    numerators = [o for o in observations if o.metric_name == numerator_metric]
    denominators = [o for o in observations if o.metric_name == denominator_metric]
    pairs = [
        (n, d)
        for n in numerators
        for d in denominators
        if n.period_start == d.period_start
        and n.period_end == d.period_end
        and n.consolidation_scope == d.consolidation_scope
        and n.currency == d.currency
        and n.scale == d.scale
        and n.inflation_adjusted == d.inflation_adjusted
    ]
    if not pairs:
        return None
    return max(pairs, key=lambda pair: pair[0].period_end)


def compute_revenue_growth_yoy(observations: tuple[FinancialObservation, ...]) -> DerivedFinancialFeature:
    revenue = [o for o in observations if o.metric_name == "revenue" and o.period_type == "ANNUAL"]
    if not revenue:
        return _unavailable("revenue_growth_yoy", "no ANNUAL revenue observations available")

    current = max(revenue, key=lambda o: o.period_end)
    candidates = [
        o
        for o in revenue
        if o is not current
        and o.consolidation_scope == current.consolidation_scope
        and o.currency == current.currency
        and o.scale == current.scale
        and o.inflation_adjusted == current.inflation_adjusted
        and o.period_end.year == current.period_end.year - 1
    ]
    if not candidates:
        return _unavailable(
            "revenue_growth_yoy",
            "no same-scope/currency/scale/inflation-status ANNUAL revenue observation for the prior year",
            input_observation_ids=(_observation_id(current),),
        )

    previous = candidates[0]
    ids = (_observation_id(current), _observation_id(previous))
    if previous.value == 0:
        return _unavailable("revenue_growth_yoy", "prior-year revenue is zero — growth rate is not meaningful", input_observation_ids=ids)

    value = (current.value - previous.value) / previous.value
    return _computed("revenue_growth_yoy", value, input_observation_ids=ids)


def compute_net_margin(observations: tuple[FinancialObservation, ...]) -> DerivedFinancialFeature:
    pair = _latest_matching_pair("revenue", "net_income", observations)
    if pair is None:
        return _unavailable(
            "net_margin",
            "no revenue/net_income observation pair sharing the exact same period, scope, currency, scale, "
            "and inflation-adjustment status",
        )

    rev, ni = pair
    ids = (_observation_id(rev), _observation_id(ni))
    if rev.value == 0:
        return _unavailable("net_margin", "revenue is zero — margin is not meaningful", input_observation_ids=ids)

    value = ni.value / rev.value
    return _computed("net_margin", value, input_observation_ids=ids)


def compute_net_debt(observations: tuple[FinancialObservation, ...]) -> DerivedFinancialFeature:
    pair = _latest_matching_pair("financial_debt", "cash_and_equivalents", observations)
    if pair is None:
        return _unavailable(
            "net_debt",
            "no financial_debt/cash_and_equivalents observation pair sharing the exact same period, scope, "
            "currency, scale, and inflation-adjustment status",
        )

    debt, cash = pair
    ids = (_observation_id(debt), _observation_id(cash))
    value = debt.value - cash.value
    return _computed("net_debt", value, input_observation_ids=ids)


def compute_debt_to_equity(observations: tuple[FinancialObservation, ...]) -> DerivedFinancialFeature:
    pair = _latest_matching_pair("financial_debt", "equity", observations)
    if pair is None:
        return _unavailable(
            "debt_to_equity",
            "no financial_debt/equity observation pair sharing the exact same period, scope, currency, scale, "
            "and inflation-adjustment status",
        )

    debt, equity = pair
    ids = (_observation_id(debt), _observation_id(equity))
    if equity.value <= 0:
        return _unavailable("debt_to_equity", "equity is zero or negative — ratio is not meaningful", input_observation_ids=ids)

    value = debt.value / equity.value
    return _computed("debt_to_equity", value, input_observation_ids=ids)


def compute_current_ratio(observations: tuple[FinancialObservation, ...]) -> DerivedFinancialFeature:
    pair = _latest_matching_pair("current_assets", "current_liabilities", observations)
    if pair is None:
        return _unavailable(
            "current_ratio",
            "no current_assets/current_liabilities observation pair sharing the exact same period, scope, "
            "currency, scale, and inflation-adjustment status",
        )

    assets, liabilities = pair
    ids = (_observation_id(assets), _observation_id(liabilities))
    if liabilities.value <= 0:
        return _unavailable(
            "current_ratio", "current liabilities is zero or negative — ratio is not meaningful", input_observation_ids=ids
        )

    value = assets.value / liabilities.value
    return _computed("current_ratio", value, input_observation_ids=ids)


def compute_operating_cash_flow_to_net_income(observations: tuple[FinancialObservation, ...]) -> DerivedFinancialFeature:
    pair = _latest_matching_pair("operating_cash_flow", "net_income", observations)
    if pair is None:
        return _unavailable(
            "operating_cash_flow_to_net_income",
            "no operating_cash_flow/net_income observation pair sharing the exact same period, scope, currency, "
            "scale, and inflation-adjustment status",
        )

    cash_flow, net_income = pair
    ids = (_observation_id(cash_flow), _observation_id(net_income))
    if net_income.value <= 0:
        return _unavailable(
            "operating_cash_flow_to_net_income",
            "net income is zero or negative — ratio is not meaningful",
            input_observation_ids=ids,
        )

    value = cash_flow.value / net_income.value
    return _computed("operating_cash_flow_to_net_income", value, input_observation_ids=ids)


def compute_interest_coverage(observations: tuple[FinancialObservation, ...]) -> DerivedFinancialFeature:
    pair = _latest_matching_pair("operating_profit", "finance_expense", observations)
    if pair is None:
        return _unavailable(
            "interest_coverage",
            "no operating_profit/finance_expense observation pair sharing the exact same period, scope, "
            "currency, scale, and inflation-adjustment status",
        )

    operating_profit, finance_expense = pair
    ids = (_observation_id(operating_profit), _observation_id(finance_expense))
    if finance_expense.value <= 0:
        return _unavailable(
            "interest_coverage", "finance expense is zero or negative — coverage ratio is not meaningful", input_observation_ids=ids
        )

    value = operating_profit.value / finance_expense.value
    return _computed("interest_coverage", value, input_observation_ids=ids)


def compute_recalculated_pe(
    observations: tuple[FinancialObservation, ...], facts: ExtractedFacts | None
) -> DerivedFinancialFeature:
    if facts is None:
        return _unavailable("recalculated_pe", "no extracted facts available")

    market_cap_fact = facts.reported_post_money_market_cap
    if market_cap_fact.status != "extracted":
        return _unavailable("recalculated_pe", f"reported_post_money_market_cap is {market_cap_fact.status}")

    market_cap_fact_id = _fact_id("reported_post_money_market_cap", market_cap_fact)
    source_ids = (market_cap_fact_id,) if market_cap_fact_id else ()

    annual_net_income = [o for o in observations if o.metric_name == "net_income" and o.period_type == "ANNUAL"]
    if not annual_net_income:
        return _unavailable("recalculated_pe", "no ANNUAL net_income observation available", source_fact_ids=source_ids)

    eligible = [
        o for o in annual_net_income if o.currency == _MARKET_CAP_ASSUMED_CURRENCY and o.scale == _MARKET_CAP_ASSUMED_SCALE
    ]
    if not eligible:
        return _unavailable(
            "recalculated_pe",
            f"no ANNUAL net_income observation stated in {_MARKET_CAP_ASSUMED_CURRENCY} "
            f"{_MARKET_CAP_ASSUMED_SCALE}s — cannot verify compatibility with reported_post_money_market_cap's units",
            input_observation_ids=tuple(_observation_id(o) for o in annual_net_income),
            source_fact_ids=source_ids,
        )

    latest = max(eligible, key=lambda o: o.period_end)
    ids = (_observation_id(latest),)
    if latest.value <= 0:
        return _unavailable(
            "recalculated_pe",
            "latest eligible annual net income is zero or negative — P/E is not meaningful",
            input_observation_ids=ids,
            source_fact_ids=source_ids,
        )

    value = market_cap_fact.value / latest.value
    return _computed("recalculated_pe", value, input_observation_ids=ids, source_fact_ids=source_ids)


def compute_reported_pe_difference_percentage(
    observations: tuple[FinancialObservation, ...], facts: ExtractedFacts | None
) -> DerivedFinancialFeature:
    recalculated = compute_recalculated_pe(observations, facts)
    if recalculated.status != "computed":
        return _unavailable(
            "reported_pe_difference_percentage",
            f"recalculated_pe is unavailable: {recalculated.unavailable_reason}",
            input_observation_ids=recalculated.input_observation_ids,
            source_fact_ids=recalculated.source_fact_ids,
        )

    if facts is None:
        return _unavailable("reported_pe_difference_percentage", "no extracted facts available")

    reported_pe_fact = facts.reported_pe
    if reported_pe_fact.status != "extracted":
        return _unavailable(
            "reported_pe_difference_percentage",
            f"reported_pe is {reported_pe_fact.status}",
            input_observation_ids=recalculated.input_observation_ids,
            source_fact_ids=recalculated.source_fact_ids,
        )

    reported_pe_fact_id = _fact_id("reported_pe", reported_pe_fact)
    source_ids = recalculated.source_fact_ids + ((reported_pe_fact_id,) if reported_pe_fact_id else ())

    if reported_pe_fact.value == 0:
        return _unavailable(
            "reported_pe_difference_percentage",
            "reported_pe is zero — percentage difference is not meaningful",
            input_observation_ids=recalculated.input_observation_ids,
            source_fact_ids=source_ids,
        )

    value = (recalculated.value - float(reported_pe_fact.value)) / float(reported_pe_fact.value) * 100
    return _computed(
        "reported_pe_difference_percentage",
        value,
        input_observation_ids=recalculated.input_observation_ids,
        source_fact_ids=source_ids,
    )


def compute_derived_financial_features(
    observations: tuple[FinancialObservation, ...],
    facts: ExtractedFacts | None,
    sector: Sector = "unknown",
) -> DerivedFinancialFeatures:
    """Compute every derived feature, then override any this
    ``sector`` doesn't support (see
    :data:`halka_arz_advisor.kap.sector.SECTOR_INAPPLICABLE_DERIVED_FEATURES`)
    with ``"not_applicable"`` — checked after computing, not instead of,
    so the override always wins regardless of what the data alone would
    have produced."""
    computed = DerivedFinancialFeatures(
        revenue_growth_yoy=compute_revenue_growth_yoy(observations),
        net_margin=compute_net_margin(observations),
        recalculated_pe=compute_recalculated_pe(observations, facts),
        reported_pe_difference_percentage=compute_reported_pe_difference_percentage(observations, facts),
        net_debt=compute_net_debt(observations),
        debt_to_equity=compute_debt_to_equity(observations),
        current_ratio=compute_current_ratio(observations),
        operating_cash_flow_to_net_income=compute_operating_cash_flow_to_net_income(observations),
        interest_coverage=compute_interest_coverage(observations),
    )

    inapplicable = SECTOR_INAPPLICABLE_DERIVED_FEATURES.get(sector, frozenset())
    if not inapplicable:
        return computed

    overrides = {
        name: _not_applicable(name, f"not a meaningful concept for sector={sector!r}")
        for name in DERIVED_FINANCIAL_FEATURE_NAMES
        if name in inapplicable
    }
    return DerivedFinancialFeatures(**{**computed.as_dict(), **overrides})
