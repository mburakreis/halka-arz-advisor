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


def test_narrative_mention_of_the_heading_is_not_mistaken_for_the_real_table():
    # Verbatim shape of a real report: a table-of-contents-style mention
    # of "Bilanço" (no period header nearby) followed, later on the same
    # page, by the real table — confirmed live to be the original bug
    # (the old code took the *first* "bilanco" match unconditionally and
    # extracted nothing). "narrative" numbers are deliberately absent
    # here since a real ToC entry has none nearby.
    text = (
        "Sekil 35 Bilanco\n"
        "2.5. Finansal Tablolar\n"
        "Bilanço\n"
        "Şirket'in genel durumu aşağıda özetlenmiştir:\n"
        "VARLIKLAR (TL) 31.12.2023 31.12.2024\n"
        "Dönen Varlıklar 100.000 200.000\n"
    )
    page = PdfPage(number=15, text=text)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-4", attachment_url="https://example/report.pdf"
    )
    current_assets = [o for o in observations if o.metric_name == "current_assets"]
    assert [o.value for o in current_assets] == [100000.0, 200000.0]


def test_narrative_sentence_listing_the_same_dates_is_not_mistaken_for_the_header():
    # Verbatim shape of a real report: a narrative sentence right before
    # the caption lists the same periods just as tightly as the real
    # header row a few dozen characters later, but with no currency
    # marker nearby — confirmed live to require trying every tight
    # period-token cluster in the header window, not just the first.
    text = (
        "Bilanço\n"
        "Şirket'in 31.12.2023, 31.12.2024 tarihli finansal durum tabloları aşağıdaki gibidir:\n"
        "VARLIKLAR (TL) 31.12.2023 31.12.2024\n"
        "Dönen Varlıklar 100.000 200.000\n"
    )
    page = PdfPage(number=15, text=text)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-5", attachment_url="https://example/report.pdf"
    )
    current_assets = [o for o in observations if o.metric_name == "current_assets"]
    assert [o.value for o in current_assets] == [100000.0, 200000.0]


def test_bilanco_heading_tolerates_a_confirmed_real_glyph_corruption():
    # Verbatim shape confirmed live in a real cached report: this
    # specific PDF's text layer renders "ç" as "~" for this heading
    # (and, elsewhere in the same document, as the digit "9") — a
    # font-encoding artifact, not a design choice. A literal "bilanço"/
    # "bilanco" search never finds this table at all.
    text = "Finansal Tablolar -Bilan~o \nmnTL 2023 2024 \nÖzsermaye 100 200\n"
    page = PdfPage(number=68, text=text)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-6", attachment_url="https://example/report.pdf"
    )
    equity = [o for o in observations if o.metric_name == "equity"]
    assert [o.value for o in equity] == [100.0, 200.0]


def test_dd_mm_yyyy_period_is_not_defaulted_to_annual_december():
    # A "30.09.2025" column must resolve to an INTERIM period ending
    # 2025-09-30 — confirmed live that the original bare-year fallback
    # silently mislabelled this shape as "ANNUAL, ending 2025-12-31"
    # (only the trailing year was ever extracted from the full date).
    text = "Gelir Tablosu (TL) 31.12.2023 30.09.2025\nHasılat 100.000 200.000\n"
    page = PdfPage(number=28, text=text)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-7", attachment_url="https://example/report.pdf"
    )
    revenue = [o for o in observations if o.metric_name == "revenue"]
    assert [o.period_type for o in revenue] == ["ANNUAL", "INTERIM"]
    assert revenue[1].period_start == date(2025, 1, 1)
    assert revenue[1].period_end == date(2025, 9, 30)


def test_projection_table_with_forecast_year_tokens_is_rejected():
    # Verbatim shape of a real report: a "Gelir Tablosu Projeksiyonu"
    # table restates real historical years alongside forecast ones
    # ("2026T", "2027T", ...) under the same "gelir tablosu" heading
    # substring — must never be silently treated as the real historical
    # table (a real, historical "Net Satışlar" row exists elsewhere on
    # the same page, and that one must win instead).
    text = (
        "Gelir Tablosu Projeksiyonu\n"
        "TL 2023 2024 2026T 2027T\n"
        "Net Satışlar 999.999 999.999 111.111 222.222\n"
        "Gelir Tablosu\n"
        "TL 2023 2024\n"
        "Net Satışlar 100.000 200.000\n"
    )
    page = PdfPage(number=38, text=text)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-8", attachment_url="https://example/report.pdf"
    )
    revenue = [o for o in observations if o.metric_name == "revenue"]
    assert [o.value for o in revenue] == [100000.0, 200000.0]


def test_row_split_across_a_page_break_with_no_repeated_caption_is_still_found():
    # Verbatim shape of a real report: the Bilanço table's header/caption
    # appears once, on the first page, and the row this test wants
    # ("Özkaynaklar") is a plain continuation of the same table on the
    # very next page, with no repeated caption at all.
    page1 = PdfPage(
        number=15,
        text=(
            "Bilanço\n"
            "VARLIKLAR (TL) 31.12.2023 31.12.2024\n"
            "Dönen Varlıklar 100.000 200.000\n"
            "Kısa Vadeli Yükümlülükler 40.000 50.000\n"
        ),
    )
    page2 = PdfPage(number=16, text="Özkaynaklar 60.000 150.000\nÖdenmiş Sermaye 20.000 20.000\n")
    observations = extract_financial_observations_from_pages(
        [page1, page2], document_type="price_determination_report", disclosure_id="d-9", attachment_url="https://example/report.pdf"
    )
    equity = [o for o in observations if o.metric_name == "equity"]
    assert [o.value for o in equity] == [60000.0, 150000.0]
    assert equity[0].source.page_number == 15  # anchored on the page the table's own header is on


def test_bare_equity_label_in_narrative_prose_is_not_mistaken_for_the_row():
    # Verbatim shape of a real report: a narrative paragraph right after
    # the table restates the same bare word ("Özkaynaklar") this
    # metric's last-resort pattern also matches, immediately followed by
    # its own list of years and monetary amounts in prose — confirmed
    # live to otherwise silently harvest nonsense values out of that
    # sentence instead of the real row.
    text = (
        "Bilanço\n"
        "VARLIKLAR (TL) 31.12.2023 31.12.2024\n"
        "Dönen Varlıklar 100.000 200.000\n"
        "Özkaynaklar. Şirket'in özkaynakları, 2023 ve 2024 yıl sonları itibarıyla sırasıyla, "
        "5,4 milyar TL ve 13,1 milyar TL olarak gerçekleşmiştir.\n"
    )
    page = PdfPage(number=15, text=text)
    observations = extract_financial_observations_from_pages(
        [page], document_type="price_determination_report", disclosure_id="d-10", attachment_url="https://example/report.pdf"
    )
    assert [o.metric_name for o in observations if o.metric_name == "equity"] == []


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
