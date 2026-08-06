from halka_arz_advisor.kap.text import fold_turkish


def test_folds_dotted_and_dotless_i():
    assert fold_turkish("İzahname") == "izahname"
    assert fold_turkish("IZAHNAME") == "izahname"
    assert fold_turkish("Satış") == "satis"


def test_folds_all_turkish_letters():
    assert fold_turkish("Şirket Öğüt Çınar Üzüm Ğamsız") == "sirket ogut cinar uzum gamsiz"


def test_naive_lower_is_wrong_for_dotted_i_demonstrating_why_fold_is_needed():
    # This is the exact pitfall fold_turkish exists to avoid.
    assert "İ".lower() != "i"


def test_fold_is_idempotent_and_case_insensitive():
    assert fold_turkish("İZAHNAME") == fold_turkish("izahname") == fold_turkish("İzahname")
