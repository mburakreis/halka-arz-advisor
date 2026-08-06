from datetime import date

from halka_arz_advisor.kap.derived_financials import (
    compute_derived_financial_features,
    compute_net_debt,
    compute_recalculated_pe,
    compute_revenue_growth_yoy,
)
from halka_arz_advisor.kap.extraction import FieldObservation, SourceRef as FactSourceRef, build_extracted_facts
from halka_arz_advisor.kap.financials import (
    FinancialObservation,
    SourceRef as ObsSourceRef,
    extract_financial_observations_from_pages,
)
from halka_arz_advisor.kap.pdf import PdfPage
from halka_arz_advisor.kap.sector import classify_sector

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


def _obs(
    metric_name: str, value: float, year: int, *, scope="standalone", currency="TRY", scale="unit", inflation=None
) -> FinancialObservation:
    return FinancialObservation(
        metric_name=metric_name,
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


# --------------------------------------------------------------------------
# Balance sheet extraction + the full derived layer for a standard company
# --------------------------------------------------------------------------

# Verbatim excerpt of the "Bilanço" (balance sheet) summary table of a
# real Fiyat Tespit Raporu, confirmed live against the cached PDF.
_BILANCO_TEXT = (
    "Bilanço\n"
    "31 Mart, 2023 2024\n"
    "TL gerçekleşmiş gerçekleşmiş\n"
    "Nakit ve Benzerleri 465.752.435 288.543.616\n"
    "Dönen Varlıklar 1.322.060.134 1.417.395.891\n"
    "Kısa Vad. Yükümlülükler 3.082.543.240 2.332.590.858\n"
    "Ana Ortak. Özkaynakları 5.365.567.088 13.079.555.028\n"
)


def test_standard_company_current_ratio_from_real_balance_sheet_table():
    page = PdfPage(number=22, text=_BILANCO_TEXT)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-std", attachment_url="https://example/report.pdf"
    )

    assert classify_sector("Örnek Enerji Yatırımları A.Ş.") == "standard"

    derived = compute_derived_financial_features(observations, None, sector="standard")

    assert derived.current_ratio.status == "computed"
    assert derived.current_ratio.value == 1417395891.0 / 2332590858.0


# --------------------------------------------------------------------------
# Sector-specific NOT_APPLICABLE
# --------------------------------------------------------------------------


def test_net_margin_is_not_applicable_for_insurance_sector():
    # Data that WOULD compute a valid net_margin for a standard company —
    # proving the "insurance" override is what blocks it, not missing data.
    observations = (_obs("revenue", 1000.0, 2024), _obs("net_income", 100.0, 2024))
    assert compute_derived_financial_features(observations, None, sector="standard").net_margin.status == "computed"

    assert classify_sector("QUİCK SİGORTA A.Ş.") == "insurance"
    result = compute_derived_financial_features(observations, None, sector="insurance").net_margin

    assert result.status == "not_applicable"
    assert result.value is None


# --------------------------------------------------------------------------
# Incompatible period
# --------------------------------------------------------------------------


def test_current_ratio_unavailable_when_periods_differ():
    observations = (
        _obs("current_assets", 300.0, 2024),
        _obs("current_liabilities", 150.0, 2023),  # a different year
    )

    result = compute_derived_financial_features(observations, None).current_ratio

    assert result.status == "unavailable"
    assert result.value is None


# --------------------------------------------------------------------------
# Provenance-preserving derived feature (net_debt)
# --------------------------------------------------------------------------


def test_net_debt_provenance_is_preserved():
    debt = _obs("financial_debt", 500.0, 2024)
    cash = _obs("cash_and_equivalents", 120.0, 2024)

    result = compute_net_debt((debt, cash))

    assert result.status == "computed"
    assert result.value == 380.0
    assert result.formula_version == "1"
    assert result.input_observation_ids == (
        "price_determination_report:d-1:p26:financial_debt:2024-01-01:2024-12-31",
        "price_determination_report:d-1:p26:cash_and_equivalents:2024-01-01:2024-12-31",
    )
