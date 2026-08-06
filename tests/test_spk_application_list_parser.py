from datetime import date

import pytest

from halka_arz_advisor.spk.application_list import parse_application_table
from halka_arz_advisor.spk.exceptions import SpkApplicationTableError


def test_header_row_is_skipped_not_counted_as_data_or_invalid(fixture_html):
    html = fixture_html("spk_application_table_page.html")
    result = parse_application_table(html)

    # 7 <tr> data-shaped rows total (rows 1-7), header is the 8th <tr> but
    # excluded entirely: 4 valid + 3 invalid == 7, not 8.
    assert len(result.records) + len(result.invalid_rows) == 7
    assert result.table_count == 1
    assert result.used_table_index == 0


def test_extracts_valid_records_with_normalized_dates(fixture_html):
    html = fixture_html("spk_application_table_page.html")
    result = parse_application_table(html)

    by_company = {r.company_name: r for r in result.records}
    assert len(result.records) == 4

    multinet_dates = sorted(r.application_date for r in result.records if r.company_name == "Multinet Kurumsal Hizmetler AŞ")
    assert multinet_dates == [date(2023, 10, 17), date(2025, 1, 20)]

    teknik = by_company["Teknik Yapı Teknik Yapılar Sanayi ve Ticaret AŞ"]
    assert teknik.application_date == date(2024, 2, 9)
    assert teknik.application_date_raw == "09.02.2024"

    son = by_company["Son Şirket AŞ"]
    assert son.application_date == date(2026, 6, 24)


def test_preserves_original_company_name_and_raw_row(fixture_html):
    html = fixture_html("spk_application_table_page.html")
    result = parse_application_table(html)

    record = next(r for r in result.records if r.company_name == "Son Şirket AŞ")
    assert record.raw_row == ("7", "Son Şirket AŞ", "24.06.2026")
    assert record.company_name == "Son Şirket AŞ"  # not upper/lowercased, no suffix stripped


def test_rejects_malformed_rows_with_clear_reasons(fixture_html):
    html = fixture_html("spk_application_table_page.html")
    result = parse_application_table(html)

    assert len(result.invalid_rows) == 3
    reasons_by_company = {row.raw_row[1]: row.reason for row in result.invalid_rows}

    assert "does not match DD.MM.YYYY" in reasons_by_company["Bozuk Tarihli Şirket AŞ"]
    assert "not a valid calendar date" in reasons_by_company["Geçersiz Takvim Şirket AŞ"]
    assert reasons_by_company[""] == "empty company name"


def test_invalid_rows_preserve_raw_row_verbatim(fixture_html):
    html = fixture_html("spk_application_table_page.html")
    result = parse_application_table(html)

    bad_date_row = next(r for r in result.invalid_rows if r.raw_row[1] == "Geçersiz Takvim Şirket AŞ")
    assert bad_date_row.raw_row == ("5", "Geçersiz Takvim Şirket AŞ", "40.13.2024")
    assert bad_date_row.row_index == 5


def test_raises_when_no_table_present():
    with pytest.raises(SpkApplicationTableError, match="no <table>"):
        parse_application_table("<html><body>no tables here</body></html>")


def test_raises_when_no_table_matches_ipo_keywords():
    html = """
    <html><body>
        <table><tr><td>foo</td><td>bar</td></tr><tr><td>1</td><td>2</td></tr></table>
    </body></html>
    """
    with pytest.raises(SpkApplicationTableError, match="none look like"):
        parse_application_table(html)
