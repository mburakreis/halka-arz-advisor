from datetime import date

from halka_arz_advisor.kap.financials import FinancialObservation, extract_financial_observations_from_pages
from halka_arz_advisor.kap.pdf import PdfPage

# Verbatim excerpt of the "Gelir Tablosu" (income statement) summary
# table (page 26) of a real Fiyat Tespit Raporu, confirmed live against
# the cached PDF before writing the extractor. "Son 4Ç" (trailing four
# quarters) has no year/year-month label and must be silently dropped
# — see kap.financials's module docstring.
_GELIR_TABLOSU_TEXT = (
    "Gelir Tablosu\n"
    "31.Ara 2023 2024 2025 2025/03 2026/03 Son 4Ç\n"
    "TL gerçekleşmiş gerçekleşmiş gerçekleşmiş gerçekleşmiş gerçekleşmiş gerçekleşmiş\n"
    "Net Satışlar 1.447.521.408 2.168.871.279 2.070.260.432 467.312.617 286.254.010 1.889.201.825\n"
    "Net Kar 3.609.020.620 849.792.937 359.014.189 44.314.067 42.311.369 357.315.893\n"
)

# Verbatim excerpt of a real insurance-holding Fiyat Tespit Raporu's
# income statement (page 71) — no "Net Satışlar"/"Hasılat" line exists
# anywhere in that report (insurers report written premiums instead, a
# genuinely different metric), only "Net Dönem Karı" — confirmed live.
_INSURER_GELIR_TABLOSU_TEXT = (
    "Quick Sigorta -Konsolide Olmayan Finansal Tablolar -Gelir Tablosu\n"
    "mnTL 2023 2024 2025 2025/03\n"
    "N-NET DONEM KARI VEYA ZARARI 3.530 3.913 8.518 1.550\n"
)


def test_extraction_succeeds_for_every_explicit_period_in_a_real_table():
    page = PdfPage(number=26, text=_GELIR_TABLOSU_TEXT)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-1", attachment_url="https://example/report.pdf"
    )

    revenue = [o for o in observations if o.metric_name == "revenue"]
    net_income = [o for o in observations if o.metric_name == "net_income"]

    # 6 numbers in each row, but only 5 periods are explicitly labelled
    # (year or year/month) — "Son 4Ç" has neither shape and is dropped,
    # never guessed at.
    assert len(revenue) == 5
    assert len(net_income) == 5
    assert [o.value for o in revenue] == [1447521408.0, 2168871279.0, 2070260432.0, 467312617.0, 286254010.0]
    assert [o.period_type for o in revenue] == ["ANNUAL", "ANNUAL", "ANNUAL", "INTERIM", "INTERIM"]
    assert revenue[0].period_start == date(2023, 1, 1)
    assert revenue[0].period_end == date(2023, 12, 31)
    assert revenue[3].period_start == date(2025, 1, 1)
    assert revenue[3].period_end == date(2025, 3, 31)


def test_missing_metric_is_absent_not_fabricated():
    # A real insurer's report: net_income is stated, revenue never is —
    # extraction must not invent a revenue observation from an unrelated
    # premium/other line.
    page = PdfPage(number=71, text=_INSURER_GELIR_TABLOSU_TEXT)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-2", attachment_url="https://example/report.pdf"
    )

    assert [o.metric_name for o in observations if o.metric_name == "revenue"] == []
    net_income = [o for o in observations if o.metric_name == "net_income"]
    assert len(net_income) == 4
    assert net_income[0].value == 3530.0
    assert net_income[0].scale == "million"  # "mnTL", not "TL" — never assumed


def test_provenance_and_period_metadata_is_preserved():
    page = PdfPage(number=71, text=_INSURER_GELIR_TABLOSU_TEXT)
    observations = extract_financial_observations_from_pages(
        [page],
        document_type="price_determination_report",
        disclosure_id="d-3",
        attachment_url="https://example/report.pdf",
        extraction_method="digital",
    )
    obs = next(o for o in observations if o.metric_name == "net_income")

    assert isinstance(obs, FinancialObservation)
    assert obs.currency == "TRY"
    assert obs.scale == "million"
    assert obs.period_type == "ANNUAL"
    assert obs.period_start == date(2023, 1, 1)
    assert obs.period_end == date(2023, 12, 31)
    # Explicit "Konsolide Olmayan" marker on this same page.
    assert obs.consolidation_scope == "standalone"
    # Never asserted without an explicit, table-adjacent label (see
    # module docstring) — honestly left unstated in this first commit.
    assert obs.inflation_adjusted is None
    assert obs.raw_snippet == "3.530"

    source = obs.source
    assert source.document_type == "price_determination_report"
    assert source.disclosure_id == "d-3"
    assert source.attachment_url == "https://example/report.pdf"
    assert source.page_number == 71
    assert source.extraction_method == "digital"
