from datetime import date

from halka_arz_advisor.notify.identity import application_identity, ipo_identity
from halka_arz_advisor.spk.application_list import SpkIpoApplicationRecord
from halka_arz_advisor.spk.models import SpkIpoRecord


def _ipo_record(**overrides) -> SpkIpoRecord:
    defaults = dict(
        ay=2,
        donem="2024 / 2",
        borsa_kodu="PATEK",
        sirket_unvani="PASİFİK TEKNOLOJİ A.Ş.",
        halka_arz_sekli=None,
        halka_arz_orani=None,
        halka_arz_fiyati_tl=None,
        ortak_satis_bin_tl=None,
        nakit_sermaye_artisi_bin_tl=None,
        ek_satis_tutari_bin_tl=None,
        satisa_hazir_bekletilen_pay_tutari_bin_tl=None,
        satisa_sunulan_toplam_tutar_bin_abd_dolari=None,
        satisa_sunulan_toplam_tutar_bin_tl=None,
        mevcut_sermaye_bin_tl=None,
        yeni_sermaye_bin_tl=None,
        satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl=None,
        ilk_islem_gordugu_pazar=None,
        halka_arza_aracilik_eden_kurum=None,
        borsada_islem_gorme_tarihi=None,
        raw={},
    )
    defaults.update(overrides)
    return SpkIpoRecord(**defaults)


def test_ipo_identity_uses_borsa_kodu_and_donem():
    record = _ipo_record(borsa_kodu="PATEK", donem="2024 / 2")
    assert ipo_identity(record) == "ipo:PATEK:2024 / 2"


def test_ipo_identity_falls_back_to_company_name_when_borsa_kodu_missing():
    record = _ipo_record(borsa_kodu=None, sirket_unvani="ÖRNEK A.Ş.", donem="2024 / 2")
    assert ipo_identity(record) == "ipo:ÖRNEK A.Ş.:2024 / 2"


def test_ipo_identity_differs_across_donem():
    a = _ipo_record(borsa_kodu="PATEK", donem="2024 / 2")
    b = _ipo_record(borsa_kodu="PATEK", donem="2025 / 1")
    assert ipo_identity(a) != ipo_identity(b)


def test_application_identity_uses_company_and_date():
    record = SpkIpoApplicationRecord(
        company_name="Multinet Kurumsal Hizmetler AŞ",
        application_date=date(2023, 10, 17),
        application_date_raw="17.10.2023",
        raw_row=("1", "Multinet Kurumsal Hizmetler AŞ", "17.10.2023"),
    )
    assert application_identity(record) == "application:Multinet Kurumsal Hizmetler AŞ:2023-10-17"


def test_application_identity_differs_for_same_company_different_dates():
    a = SpkIpoApplicationRecord(
        company_name="X AŞ", application_date=date(2023, 1, 1), application_date_raw="01.01.2023", raw_row=()
    )
    b = SpkIpoApplicationRecord(
        company_name="X AŞ", application_date=date(2024, 1, 1), application_date_raw="01.01.2024", raw_row=()
    )
    assert application_identity(a) != application_identity(b)
