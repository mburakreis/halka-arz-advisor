from datetime import date, datetime

from halka_arz_advisor.kap.matching import match_disclosure, normalize_company_name
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.notify.identity import application_identity, ipo_identity
from halka_arz_advisor.spk.application_list import SpkIpoApplicationRecord
from halka_arz_advisor.spk.models import SpkIpoRecord


def _disclosure(**overrides) -> KapDisclosure:
    defaults = dict(
        disclosure_id="d1",
        disclosure_index=1,
        published_at=datetime(2026, 1, 1),
        company_name="PASİFİK TEKNOLOJİ A.Ş.",
        ticker="PATEK",
        title="İzahname (SPK Tarafından Onaylanan)",
        summary="",
        document_type="approved_prospectus",
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(),
        matched_spk_record_id=None,
        match_method="unmatched",
        raw={},
    )
    defaults.update(overrides)
    return KapDisclosure(**defaults)


def _ipo_record(**overrides) -> SpkIpoRecord:
    defaults = dict(
        ay=2, donem="2026 / 2", borsa_kodu="PATEK", sirket_unvani="PASİFİK TEKNOLOJİ A.Ş.",
        halka_arz_sekli=None, halka_arz_orani=None, halka_arz_fiyati_tl=None,
        ortak_satis_bin_tl=None, nakit_sermaye_artisi_bin_tl=None, ek_satis_tutari_bin_tl=None,
        satisa_hazir_bekletilen_pay_tutari_bin_tl=None, satisa_sunulan_toplam_tutar_bin_abd_dolari=None,
        satisa_sunulan_toplam_tutar_bin_tl=None, mevcut_sermaye_bin_tl=None, yeni_sermaye_bin_tl=None,
        satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl=None, ilk_islem_gordugu_pazar=None,
        halka_arza_aracilik_eden_kurum=None, borsada_islem_gorme_tarihi=None, raw={},
    )
    defaults.update(overrides)
    return SpkIpoRecord(**defaults)


def _application_record(**overrides) -> SpkIpoApplicationRecord:
    defaults = dict(
        company_name="Pasifik Teknoloji AŞ",
        application_date=date(2025, 11, 1),
        application_date_raw="01.11.2025",
        raw_row=(),
    )
    defaults.update(overrides)
    return SpkIpoApplicationRecord(**defaults)


# --------------------------------------------------------------------------
# normalize_company_name
# --------------------------------------------------------------------------


def test_normalize_strips_all_controlled_suffixes():
    assert normalize_company_name("PASİFİK TEKNOLOJİ A.Ş.") == normalize_company_name("Pasifik Teknoloji AŞ")
    assert normalize_company_name("X SANAYİ VE TİCARET A.Ş.") == normalize_company_name("X Anonim Şirketi")


def test_normalize_handles_sanayii_ticareti_spelling_variant():
    assert normalize_company_name("KARSU TEKSTİL SANAYİİ VE TİCARET A.Ş.") == normalize_company_name(
        "Karsu Tekstil Sanayi ve Ticaret AŞ"
    )


# --------------------------------------------------------------------------
# match_disclosure
# --------------------------------------------------------------------------


def test_exact_ticker_match():
    disclosure = _disclosure(ticker="PATEK")
    record = _ipo_record(borsa_kodu="PATEK")

    result = match_disclosure(disclosure, ipo_records=[record])

    assert result.match_method == "ticker"
    assert result.matched_spk_record_id == ipo_identity(record)


def test_ticker_match_is_case_insensitive():
    disclosure = _disclosure(ticker="patek")
    record = _ipo_record(borsa_kodu="PATEK")

    result = match_disclosure(disclosure, ipo_records=[record])
    assert result.match_method == "ticker"


def test_falls_back_to_company_name_when_no_ticker():
    disclosure = _disclosure(ticker=None, company_name="Pasifik Teknoloji AŞ")
    record = _ipo_record(borsa_kodu="OTHER", sirket_unvani="PASİFİK TEKNOLOJİ A.Ş.")

    result = match_disclosure(disclosure, ipo_records=[record])

    assert result.match_method == "company_name"
    assert result.matched_spk_record_id == ipo_identity(record)


def test_company_name_match_against_application_records():
    disclosure = _disclosure(ticker=None, company_name="Pasifik Teknoloji AŞ")
    record = _application_record(company_name="PASİFİK TEKNOLOJİ A.Ş.")

    result = match_disclosure(disclosure, application_records=[record])

    assert result.match_method == "company_name"
    assert result.matched_spk_record_id == application_identity(record)


def test_unmatched_ticker_does_not_fall_back_to_company_name():
    """Regression test: company_name is always the *filer* (companyTitle),
    which can be a different entity than the ticker (preferentially
    sourced from relatedStocks — the actual IPO subject for
    intermediary-filed disclosures). Falling back from an unmatched
    ticker to company-name previously produced a real false positive
    against live data: a disclosure about "Bewen Enerji A.Ş." (ticker
    BEWEN, not yet a completed IPO) filed by "Marbaş Menkul Değerler
    A.Ş." incorrectly matched Marbaş's own unrelated SPK application
    record, since Marbaş itself happened to also be an applicant."""
    disclosure = _disclosure(ticker="NOPE", company_name="Pasifik Teknoloji AŞ")
    record = _ipo_record(borsa_kodu="PATEK", sirket_unvani="PASİFİK TEKNOLOJİ A.Ş.")

    result = match_disclosure(disclosure, ipo_records=[record])

    assert result.match_method == "unmatched"
    assert result.matched_spk_record_id is None


def test_ambiguous_ticker_across_multiple_records_is_not_auto_matched():
    disclosure = _disclosure(ticker="DUP")
    record_a = _ipo_record(borsa_kodu="DUP", donem="2026 / 1", sirket_unvani="A AŞ")
    record_b = _ipo_record(borsa_kodu="DUP", donem="2026 / 2", sirket_unvani="B AŞ")

    result = match_disclosure(disclosure, ipo_records=[record_a, record_b])

    assert result.match_method == "unmatched"
    assert result.matched_spk_record_id is None


def test_ambiguous_company_name_across_multiple_records_is_not_auto_matched():
    disclosure = _disclosure(ticker=None, company_name="Ortak İsim AŞ")
    record_a = _ipo_record(borsa_kodu="AAA", sirket_unvani="ORTAK İSİM A.Ş.")
    record_b = _application_record(company_name="Ortak İsim Anonim Şirketi")

    result = match_disclosure(disclosure, ipo_records=[record_a], application_records=[record_b])

    assert result.match_method == "unmatched"
    assert result.matched_spk_record_id is None


def test_completely_unrelated_disclosure_is_unmatched():
    disclosure = _disclosure(ticker="ZZZZ", company_name="Bilinmeyen Şirket AŞ")
    record = _ipo_record(borsa_kodu="PATEK", sirket_unvani="PASİFİK TEKNOLOJİ A.Ş.")

    result = match_disclosure(disclosure, ipo_records=[record])

    assert result.match_method == "unmatched"
    assert result.matched_spk_record_id is None


def test_no_spk_records_at_all_is_unmatched():
    disclosure = _disclosure()
    result = match_disclosure(disclosure)
    assert result.match_method == "unmatched"
    assert result.matched_spk_record_id is None


def test_intermediary_filed_disclosure_does_not_match_the_filers_own_unrelated_application():
    """Reproduces the exact real-data false positive this rule prevents:
    a price-determination report about Bewen Enerji (ticker extracted
    from relatedStocks), filed by Marbaş Menkul Değerler — which
    separately, coincidentally, has its own unrelated SPK application."""
    disclosure = _disclosure(
        ticker="BEWEN",  # extracted from relatedStocks — not yet a completed IPO
        company_name="MARBAŞ MENKUL DEĞERLER A.Ş.",  # the filer, unrelated to Bewen
        document_type="price_determination_report",
    )
    marbas_own_application = _application_record(company_name="Marbaş Menkul Değerler AŞ")

    result = match_disclosure(disclosure, application_records=[marbas_own_application])

    assert result.match_method == "unmatched"
    assert result.matched_spk_record_id is None
