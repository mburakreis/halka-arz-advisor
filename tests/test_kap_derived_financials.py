from datetime import date

from halka_arz_advisor.kap.derived_financials import (
    compute_recalculated_pe,
    compute_revenue_growth_yoy,
)
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef as FactSourceRef, build_extracted_facts
from halka_arz_advisor.kap.financials import FinancialObservation, SourceRef as ObsSourceRef

_SRC = ObsSourceRef("price_determination_report", "d-1", "https://example/report.pdf", 26, "digital")


def _revenue(value: float, year: int, *, scope="standalone", currency="TRY", scale="unit", inflation=None) -> FinancialObservation:
    return FinancialObservation(
        metric_name="revenue",
        value=value,
        currency=currency,
        scale=scale,
        period_start=date(year, 1, 1),
        period_end=date(year, 12, 31),
        period_type="ANNUAL",
        consolidation_scope=scope,
        inflation_adjusted=inflation,
        raw_snippet=str(value),
        source=_SRC,
    )


def test_revenue_growth_yoy_computes_from_two_comparable_annual_periods():
    observations = (_revenue(1000.0, 2023), _revenue(1250.0, 2024))

    result = compute_revenue_growth_yoy(observations)

    assert result.status == "computed"
    assert result.value == (1250.0 - 1000.0) / 1000.0
    assert len(result.input_observation_ids) == 2
    assert result.formula_version == "1"


def test_revenue_growth_yoy_unavailable_when_scope_differs():
    # Same years, but one consolidated and one standalone — must not be
    # silently combined into a growth rate.
    observations = (
        _revenue(1000.0, 2023, scope="consolidated"),
        _revenue(1250.0, 2024, scope="standalone"),
    )

    result = compute_revenue_growth_yoy(observations)

    assert result.status == "unavailable"
    assert result.value is None
    assert "prior year" in result.unavailable_reason


def test_recalculated_pe_provenance_is_preserved():
    market_cap_source = FactSourceRef("price_determination_report", "d-1", "https://example/report.pdf", 8, "digital")
    facts = build_extracted_facts(
        None,
        None,
        None,
        {
            "reported_post_money_market_cap": FieldObservation(236.2, "Nihai Değer 236,2", market_cap_source),
        },
    )
    net_income = FinancialObservation(
        metric_name="net_income",
        value=20.0,
        currency="USD",
        scale="million",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31),
        period_type="ANNUAL",
        consolidation_scope="standalone",
        inflation_adjusted=None,
        raw_snippet="20.0",
        source=_SRC,
    )

    result = compute_recalculated_pe((net_income,), facts)

    assert result.status == "computed"
    assert result.value == 236.2 / 20.0
    assert result.formula_version == "1"
    assert result.input_observation_ids == (
        f"price_determination_report:d-1:p26:net_income:2025-01-01:2025-12-31",
    )
    assert result.source_fact_ids == ("price_determination_report:d-1:p8:reported_post_money_market_cap",)
