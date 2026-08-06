from halka_arz_advisor.kap.classification import classify_title, target_document_types


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
