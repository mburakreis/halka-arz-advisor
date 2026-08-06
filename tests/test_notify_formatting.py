from datetime import date, datetime

from halka_arz_advisor.notify.formatting import format_application_notification, format_ipo_notification
from halka_arz_advisor.spk.application_list import SpkIpoApplicationRecord
from halka_arz_advisor.spk.models import SpkIpoRecord


def _ipo_record(**overrides) -> SpkIpoRecord:
    defaults = dict(
        ay=2, donem="2024 / 2", borsa_kodu="PATEK", sirket_unvani="PASİFİK TEKNOLOJİ A.Ş.",
        halka_arz_sekli=None, halka_arz_orani=20.15, halka_arz_fiyati_tl=35.0,
        ortak_satis_bin_tl=None, nakit_sermaye_artisi_bin_tl=None, ek_satis_tutari_bin_tl=None,
        satisa_hazir_bekletilen_pay_tutari_bin_tl=None, satisa_sunulan_toplam_tutar_bin_abd_dolari=None,
        satisa_sunulan_toplam_tutar_bin_tl=None, mevcut_sermaye_bin_tl=None, yeni_sermaye_bin_tl=None,
        satisa_sunulan_toplam_tutar_piyasa_degeri_bin_tl=None, ilk_islem_gordugu_pazar=None,
        halka_arza_aracilik_eden_kurum=None, borsada_islem_gorme_tarihi=datetime(2024, 2, 13),
        raw={},
    )
    defaults.update(overrides)
    return SpkIpoRecord(**defaults)


def test_format_ipo_notification_includes_all_key_fields():
    record = _ipo_record()
    text = format_ipo_notification(record)

    assert text.startswith("Yeni halka arz:")
    assert "PASİFİK TEKNOLOJİ A.Ş." in text
    assert "PATEK" in text
    assert "35.0" in text
    assert "20.15" in text
    assert "2024-02-13" in text


def test_format_ipo_notification_omits_missing_optional_fields():
    record = _ipo_record(halka_arz_fiyati_tl=None, halka_arz_orani=None, borsada_islem_gorme_tarihi=None)
    text = format_ipo_notification(record)

    assert "Fiyat:" not in text
    assert "oranı" not in text
    assert "İşlem tarihi" not in text
    assert "PATEK" in text


def test_format_ipo_notification_handles_missing_ticker():
    record = _ipo_record(borsa_kodu=None)
    text = format_ipo_notification(record)
    assert "PASİFİK TEKNOLOJİ A.Ş." in text
    assert "()" not in text


def test_format_application_notification():
    record = SpkIpoApplicationRecord(
        company_name="Multinet Kurumsal Hizmetler AŞ",
        application_date=date(2023, 10, 17),
        application_date_raw="17.10.2023",
        raw_row=("1", "Multinet Kurumsal Hizmetler AŞ", "17.10.2023"),
    )
    text = format_application_notification(record)

    assert text == (
        "Yeni halka arz başvurusu:\n"
        "Multinet Kurumsal Hizmetler AŞ\n"
        "Başvuru tarihi: 2023-10-17"
    )
