from datetime import datetime

import pytest

from halka_arz_advisor.kap.exceptions import KapSchemaError
from halka_arz_advisor.kap.models import parse_disclosure


def test_parses_self_filed_izahname(fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    disclosure = parse_disclosure(sample[0])

    assert disclosure.disclosure_id == "kap-id-izahname-cvkmd"
    assert disclosure.company_name == "CVK MADEN İŞLETMELERİ SANAYİ VE TİCARET A.Ş."
    assert disclosure.ticker == "CVKMD"
    assert disclosure.title == "İzahname (SPK Tarafından Onaylanan)"
    assert disclosure.document_type == "approved_prospectus"
    assert disclosure.published_at == datetime(2026, 8, 3, 13, 21, 39)
    assert disclosure.notification_url == "https://www.kap.org.tr/tr/Bildirim/1641990"
    assert disclosure.attachment_urls == ()
    assert disclosure.matched_spk_record_id is None
    assert disclosure.match_method == "unmatched"
    assert disclosure.raw == sample[0]


def test_ticker_prefers_single_valued_related_stocks_over_filer_stock_code(fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    disclosure = parse_disclosure(sample[1])  # Halka Arz Sonuçları filed by Garanti Yatırım, about QUICK

    assert disclosure.company_name == "GARANTİ YATIRIM MENKUL KIYMETLER A.Ş."  # the filer, preserved as-is
    assert disclosure.ticker == "QUICK"  # the actual IPO company, from relatedStocks
    assert disclosure.document_type == "ipo_results"


def test_ticker_falls_back_to_stock_code_when_related_stocks_is_ambiguous(fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    disclosure = parse_disclosure(sample[2])  # relatedStocks has 4 comma-separated tickers, stockCode is single

    assert disclosure.ticker == "VKY"  # falls back to the filer's own single-valued stockCode
    assert disclosure.document_type == "price_determination_report"


def test_ticker_is_none_when_both_related_stocks_and_stock_code_are_ambiguous():
    disclosure = parse_disclosure(
        {
            "disclosureBasic": {
                "disclosureId": "id-both-ambiguous",
                "title": "Fiyat Tespit Raporu",
                "publishDate": "01.01.2026 00:00:00",
                "companyTitle": "ÖRNEK YATIRIM A.Ş.",
                "stockCode": "ORY, ORNYT",
                "relatedStocks": "A1CAP, ACP, KARCL, ZRY",
            }
        }
    )
    assert disclosure.ticker is None


def test_parses_trading_start_notice_from_borsa_istanbul(fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    disclosure = parse_disclosure(sample[4])

    assert disclosure.company_name == "BORSA İSTANBUL A.Ş."
    assert disclosure.ticker == "PATEK"  # single-valued relatedStocks
    assert disclosure.document_type == "trading_start"


def test_other_disclosure_classified_correctly(fixture_json):
    sample = fixture_json("kap_disclosures_sample.json")
    disclosure = parse_disclosure(sample[5])
    assert disclosure.document_type == "other"


def test_raises_on_non_object_item():
    with pytest.raises(KapSchemaError, match="is not a JSON object"):
        parse_disclosure(["not", "an", "object"])


def test_raises_when_disclosure_basic_missing():
    with pytest.raises(KapSchemaError, match="disclosureBasic"):
        parse_disclosure({"disclosureDetail": {}})


def test_raises_when_disclosure_id_missing():
    with pytest.raises(KapSchemaError, match="disclosureId"):
        parse_disclosure({"disclosureBasic": {"title": "X", "publishDate": "01.01.2026 00:00:00", "companyTitle": "X"}})


def test_raises_when_title_missing():
    with pytest.raises(KapSchemaError, match="title"):
        parse_disclosure(
            {"disclosureBasic": {"disclosureId": "id1", "publishDate": "01.01.2026 00:00:00", "companyTitle": "X"}}
        )


def test_raises_on_unparsable_publish_date():
    with pytest.raises(KapSchemaError, match="publishDate"):
        parse_disclosure(
            {
                "disclosureBasic": {
                    "disclosureId": "id1",
                    "title": "X",
                    "publishDate": "not-a-date",
                    "companyTitle": "X",
                }
            }
        )
