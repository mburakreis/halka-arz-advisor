from datetime import date, datetime

from halka_arz_advisor.decision.pipeline import resolve_company_identity
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.notify.identity import application_identity, ipo_identity
from halka_arz_advisor.spk.application_list import SpkIpoApplicationRecord
from halka_arz_advisor.spk.models import SpkIpoRecord


def _disclosure(**overrides) -> KapDisclosure:
    defaults = dict(
        disclosure_id="d1",
        disclosure_index=1,
        published_at=datetime(2026, 7, 24),
        # The KAP filer's own companyTitle for a price-determination
        # report/IPO-results disclosure is typically the lead
        # intermediary brokerage, not the issuer — exactly the case this
        # module must never surface as a display name.
        company_name="MAHER MENKUL DEĞERLER A.Ş.",
        ticker="QUICK",
        title="Fiyat Tespit Raporu",
        summary="",
        document_type="price_determination_report",
        notification_url="https://www.kap.org.tr/tr/Bildirim/1",
        attachment_urls=(),
        matched_spk_record_id="ipo:QUICK:2026 / 8",
        match_method="ticker",
        raw={},
    )
    defaults.update(overrides)
    return KapDisclosure(**defaults)


def _ipo_record(**overrides) -> SpkIpoRecord:
    defaults = dict(
        ay=8, donem="2026 / 8", borsa_kodu="QUICK", sirket_unvani="Quick Sigorta A.Ş.",
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
        company_name="Quick Sigorta AŞ",
        application_date=date(2024, 9, 25),
        application_date_raw="25.09.2024",
        raw_row=(),
    )
    defaults.update(overrides)
    return SpkIpoApplicationRecord(**defaults)


def test_completed_ipo_record_name_and_ticker_are_authoritative():
    ipo_record = _ipo_record()
    disclosure = _disclosure()
    record_id = ipo_identity(ipo_record)

    name, ticker = resolve_company_identity(record_id, [disclosure], ipo_records=[ipo_record], application_records=[])

    assert name == "Quick Sigorta A.Ş."
    assert name != disclosure.company_name
    assert ticker == "QUICK"


def test_application_record_name_is_authoritative_when_no_completed_ipo_yet():
    application_record = _application_record()
    record_id = application_identity(application_record)
    # A disclosure filed by the lead broker, matched to the application
    # by ticker (see halka_arz_advisor.kap.matching's own precedent).
    disclosure = _disclosure(matched_spk_record_id=record_id)

    name, ticker = resolve_company_identity(record_id, [disclosure], ipo_records=[], application_records=[application_record])

    assert name == "Quick Sigorta AŞ"
    assert name != disclosure.company_name
    # No ticker on the application record itself — falls back to
    # whatever the disclosure carries (KAP resolves this reliably
    # regardless of document type/filer).
    assert ticker == "QUICK"


def test_falls_back_to_disclosure_heuristic_when_no_spk_match_exists():
    """A genuinely pre-application company (no SPK record at all yet)
    still needs a best-effort name — the existing disclosure-priority
    heuristic, unchanged."""
    disclosure = _disclosure(
        document_type="approved_prospectus", company_name="Quick Sigorta A.Ş.", matched_spk_record_id="ipo:QUICK:unknown"
    )

    name, ticker = resolve_company_identity("ipo:QUICK:unknown", [disclosure], ipo_records=[], application_records=[])

    assert name == "Quick Sigorta A.Ş."
    assert ticker == "QUICK"
