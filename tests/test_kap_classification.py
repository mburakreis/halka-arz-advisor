from halka_arz_advisor.kap.classification import classify_prospectus_document_role, classify_title, target_document_types


def test_izahname_classifies_as_approved_prospectus():
    assert classify_title("İzahname (SPK Tarafından Onaylanan)") == "approved_prospectus"
    assert classify_title("Onaylı İzahname") == "approved_prospectus"
    assert classify_title("İzahname-Özet (SPK Tarafından Onaylanan)") == "approved_prospectus"
    assert classify_title("İzahname veya İzahnameyi Oluşturan Belgelerde Değişiklik - Ekleme") == "approved_prospectus"


def test_tasarruf_sahiplerine_satis_duyurusu_classifies_as_investor_sale_announcement():
    assert classify_title("Tasarruf Sahiplerine Satış Duyurusu") == "investor_sale_announcement"


def test_fiyat_tespit_raporu_classifies_as_price_determination_report():
    assert classify_title("Fiyat Tespit Raporu") == "price_determination_report"


def test_analyst_or_evaluation_review_of_price_report_is_a_separate_type():
    """Regression guard: these must NOT be classified as the official report."""
    assert (
        classify_title(
            "Fiyat Tespit Raporuna İlişkin Analist Raporu (Halka Arza Aracılık Eden Kuruluş Tarafından Hazırlanan)"
        )
        == "price_determination_review"
    )
    assert (
        classify_title(
            "Fiyat Tespit Raporuna İlişkin Analist Raporu "
            "(Halka Arza Aracılık Eden Kuruluş Dışında Farklı bir Kuruluş Tarafından Hazırlanan)"
        )
        == "price_determination_review"
    )
    assert classify_title("Fiyat Tespit Raporuna İlişkin Değerlendirme Raporu") == "price_determination_review"


def test_price_determination_review_is_excluded_from_target_document_types():
    assert "price_determination_review" not in target_document_types()


def test_halka_arz_sonuclari_classifies_as_ipo_results():
    assert classify_title("Halka Arz Sonuçları") == "ipo_results"


def test_islem_gormeye_baslama_classifies_as_trading_start():
    assert classify_title("İşlem Görmeye Başlama") == "trading_start"
    assert classify_title("Payların İşlem Görmeye Başlaması") == "trading_start"
    assert classify_title("Varantların veya Sertifikaların İşlem Görmeye Başlaması") == "trading_start"


def test_unrelated_title_classifies_as_other():
    assert classify_title("Genel Kurul İşlemlerine İlişkin Bildirim") == "other"
    assert classify_title("Finansal Rapor") == "other"


def test_classification_is_case_insensitive():
    assert classify_title("İZAHNAME (SPK TARAFINDAN ONAYLANAN)") == "approved_prospectus"
    assert classify_title("izahname (spk tarafından onaylanan)") == "approved_prospectus"
    assert classify_title("fiyat TESPİT raporu") == "price_determination_report"


def test_classification_is_robust_to_turkish_characters():
    # Same phrase, ASCII-only spelling (no Turkish diacritics) — must still match.
    assert classify_title("Halka Arz Sonuclari") == "ipo_results"
    assert classify_title("Islem Gormeye Baslama") == "trading_start"


def test_prospectus_role_recognizes_base_document_and_its_parts_and_revisions():
    """Real summaries confirmed live (2026-08-07) across several tickers'
    approved_prospectus disclosures — the base document itself, split
    across several filings and/or wholly reposted as a correction."""
    assert classify_prospectus_document_role("ŞA-RA Enerji İnşaat Ticaret ve Sanayi A.Ş. İzahname") == "base_document"
    assert classify_prospectus_document_role("ŞA-RA Enerji İnşaat Ticaret ve Sanayi A.Ş. İzahname - Düzeltme") == "base_document"
    assert classify_prospectus_document_role("Albayrak Hazır Beton A.Ş. İzahname - 1. Bölüm") == "base_document"
    assert classify_prospectus_document_role("Albayrak Hazır Beton A.Ş.- İzahname") == "base_document"
    assert (
        classify_prospectus_document_role("Golda Gıda Sanayi ve Ticaret A.Ş.'nin SPK tarafından onaylanan izahnamesinin 1. kısmı")
        == "base_document"
    )
    assert classify_prospectus_document_role("Masfen Enerji Anonim Şirketi Paylarının Halka Arzına İlişkin SPK Onaylı İzahname-2") == "base_document"
    assert (
        classify_prospectus_document_role(
            "Masfen Enerji A.Ş. onaylı halka arz izahnamesinin sehven eksik yayımlanan 412. Sayfası ve "
            "tek metin haline getirilmiş onaylı izahname 1"
        )
        == "base_document"
    )
    assert classify_prospectus_document_role("Quick Sigorta A.Ş. Paylarının Halka Arzına İlişkin İzahname") == "base_document"


def test_prospectus_role_recognizes_exhibits_as_attachments_not_the_base_document():
    """Same real-ticker evidence: valuation/audit/legal/charter/fund-use
    reports and appendix bundles filed under the identical KAP
    'approved_prospectus' classification, distinguishable only by
    summary text — never by title, which KAP files identically."""
    assert classify_prospectus_document_role("EK_1 Şirket Esas Sözleşme") == "attachment"
    assert classify_prospectus_document_role("Ekinciler Demir ve Çelik Sanayi AŞ Halka Arzına İlişkin Onaylı İzahname Ek-1") == "attachment"
    assert (
        classify_prospectus_document_role("Golda Gıda Sanayi ve Ticaret A.Ş.'nin SPK onaylı İzahnamesi ekleri 1. Bölüm")
        == "attachment"
    )
    assert classify_prospectus_document_role("Albayrak Hazır Beton A.Ş.Izahname ekleri") == "attachment"
    assert (
        classify_prospectus_document_role(
            "ŞA-RA Enerji İnşaat Ticaret ve Sanayi A.Ş. Paylarının Halka Arzına İlişkin İzahname EK-7 Fiyat Tespit Raporu 31.03"
        )
        == "attachment"
    )
    assert classify_prospectus_document_role("Masfen Enerji Anonim Şirketi Paylarının Halka Arzına İlişkin Esas Sözleşme") == "attachment"
    assert (
        classify_prospectus_document_role("Quick Sigorta A.Ş. Paylarının Halka Arzına İlişkin Gayrimenkul Değerleme Raporları") == "attachment"
    )
    assert classify_prospectus_document_role("Quick Sigorta A.Ş. Paylarının Halka Arzına İlişkin Bağımsız Denetim Sorumluluk Beyanları") == "attachment"
    # No "izahname" mention at all — never the base document, no keyword needed.
    assert classify_prospectus_document_role("ŞA-RA Enerji İnşaat Ticaret ve Sanayi A.Ş. GK İç Yönergesi TTSG") == "attachment"


def test_prospectus_role_ek_marker_does_not_false_positive_on_eksik():
    """'eksik' (missing/incomplete) starts with the same three letters as
    the 'Ek-N' appendix marker but is unrelated — must not misclassify a
    genuine base-document correction notice as an exhibit."""
    assert (
        classify_prospectus_document_role(
            "Masfen Enerji A.Ş. onaylı halka arz izahnamesinin sehven eksik yayımlanan 412. Sayfası"
        )
        == "base_document"
    )


def test_target_document_types_excludes_other():
    types = target_document_types()
    assert "other" not in types
    assert set(types) == {
        "approved_prospectus",
        "investor_sale_announcement",
        "price_determination_report",
        "ipo_results",
        "trading_start",
    }
    assert len(types) == 5
