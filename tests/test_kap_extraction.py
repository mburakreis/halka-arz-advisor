from datetime import date

import pytest

from halka_arz_advisor.kap.extraction import (
    FIELD_NAMES,
    FieldObservation,
    SourceRef,
    build_extracted_facts,
    extract_capital_increase_ratio,
    extract_capital_increase_shares,
    extract_currency,
    extract_distribution_method,
    extract_investor_group_allocations,
    extract_key_risk_items,
    extract_observations_from_pages,
    extract_offering_price,
    extract_par_value_per_share,
    extract_post_offer_capital,
    extract_pre_offer_capital,
    extract_reported_ev_ebitda,
    extract_reported_ps,
    extract_retail_allocated_shares,
    extract_retail_demand_multiple,
    extract_secondary_sale_ratio,
    extract_secondary_sale_shares,
    extract_subscription_dates,
    extract_total_demand_multiple,
    extract_total_offered_shares,
    extract_use_of_proceeds,
    merge_field_observations,
    parse_turkish_date,
    parse_turkish_number,
)
from halka_arz_advisor.kap.pdf import PdfPage

# --------------------------------------------------------------------------
# Turkish normalization
# --------------------------------------------------------------------------


def test_parse_turkish_number_thousands_and_decimal():
    assert parse_turkish_number("2.380.000.000") == 2380000000.0
    assert parse_turkish_number("25,03") == 25.03
    assert parse_turkish_number("76,60") == 76.60


def test_parse_turkish_number_invalid_returns_none():
    assert parse_turkish_number("not a number") is None


def test_parse_turkish_date_dots_and_slashes():
    assert parse_turkish_date("22.07.2026") == date(2026, 7, 22)
    assert parse_turkish_date("22/07/2026") == date(2026, 7, 22)


def test_parse_turkish_date_invalid_calendar_date_returns_none():
    assert parse_turkish_date("31.02.2026") is None


def test_parse_turkish_date_garbage_returns_none():
    assert parse_turkish_date("not a date") is None


# --------------------------------------------------------------------------
# Subscription dates
# --------------------------------------------------------------------------


def test_extract_subscription_dates_real_shape():
    text = "Talep toplama 22.07.2026 - 24.07.2026 tarihleri arasında gerçekleştirilecektir."
    start, end = extract_subscription_dates(text)
    assert start[0] == date(2026, 7, 22)
    assert end[0] == date(2026, 7, 24)
    assert "22.07.2026" in start[1] and "24.07.2026" in start[1]


def test_extract_subscription_dates_case_insensitive_and_ascii_folded():
    text = "TALEP TOPLAMA 01.03.2026 - 03.03.2026 seklinde yapilacaktir."
    start, end = extract_subscription_dates(text)
    assert start[0] == date(2026, 3, 1)
    assert end[0] == date(2026, 3, 3)


def test_extract_subscription_dates_not_found_returns_none_pair():
    assert extract_subscription_dates("bu metinde talep toplama tarihi yok") == (None, None)


def test_extract_subscription_dates_halka_arz_suresi_anchor_across_line_wraps():
    # Real shape (folded/paraphrased from QUICK's and MASFN's actual
    # 2026 investor sale announcements, OCR'd): the "Halka Arz Süresi"
    # heading, not "talep toplama", and the date range wrapped onto a
    # later line — the anchor and dates are never on the same line.
    text = (
        "Halka Arz Süresi: Halka arz edilecek olan 48.312.950 TL nominal değerli 48.312.950 adet\n"
        "nama yazılı paylar 29.07.2026 ile31.07.2026 tarihleri arasında 3 iş günü süreyle satışa\n"
        "sunulacaktır."
    )
    start, end = extract_subscription_dates(text)
    assert start[0] == date(2026, 7, 29)
    assert end[0] == date(2026, 7, 31)
    assert "Halka Arz Süresi" in start[1]


def test_extract_subscription_dates_halka_arz_suresi_no_space_before_ile():
    # "ile" glued directly onto the following date with no space is a
    # real OCR artifact observed live — must still parse.
    text = "Halka Arz Süresi: paylar 01.08.2026 ile03.08.2026 tarihleri arasında satışa sunulacaktır."
    start, end = extract_subscription_dates(text)
    assert start[0] == date(2026, 8, 1)
    assert end[0] == date(2026, 8, 3)


def test_extract_subscription_dates_trailing_anchor_when_heading_is_lost_to_ocr():
    # Real shape (folded/paraphrased from GOLDA's actual 2026
    # announcement, OCR'd): the "Halka Arz Süresi" heading itself did
    # not survive OCR at all — only the sentence body did — so the
    # heading-anchored pattern alone would never match even though the
    # date range OCR'd cleanly. The trailing "tarihleri arasında ...
    # satışa sunulacaktır" grammar is what actually anchors this case.
    text = (
        "alka arz edilecek olan 87.499.998 TL nominal degerli 87.499.998 adet\n"
        "B Grubu hamiline yazılı paylar 01/07/2026 ile 02/07/2026 tarihleri arasında 2 iş günü süreyle\n"
        "satışa sunulacaktır."
    )
    start, end = extract_subscription_dates(text)
    assert start[0] == date(2026, 7, 1)
    assert end[0] == date(2026, 7, 2)


def test_extract_subscription_dates_trailing_anchor_ignores_unrelated_date_range():
    # Real false-positive risk (folded/paraphrased from METEN's actual
    # 44-page prospectus): a bare "DATE ile DATE tarihleri arasında" can
    # appear for something completely unrelated (here, an EPDK
    # electricity-tariff period) — the trailing pattern must not match
    # this, since there is no "satışa sunulacaktır" nearby.
    text = "06.01.2017 ile 01.03.2017 tarihleri arasında 500 TL/MWh olarak belirlenmiştir."
    assert extract_subscription_dates(text) == (None, None)


# --------------------------------------------------------------------------
# Offering price / currency
# --------------------------------------------------------------------------


def test_extract_offering_price_narrative_sentence():
    text = "Halka arz satış fiyatı olarak belirlenen 76,60 TL, hesaplanan pay başına fiyat olan 102,17 TL."
    value, snippet = extract_offering_price(text)
    assert value == 76.60
    assert "belirlenen 76,60 TL" in snippet


def test_extract_offering_price_label_form():
    text = "Halka Arz Fiyatı (TL): 45,00"
    value, snippet = extract_offering_price(text)
    assert value == 45.00


def test_extract_offering_price_not_found():
    assert extract_offering_price("bu belgede fiyat bilgisi yoktur") is None


def test_extract_offering_price_par_value_sentence_real_shape():
    # Real shape (paraphrased from four live 2026 investor sale
    # announcements — ATATR, EMPAE, MEYSU, NETCD): this is where every
    # one of them actually states the offer price, never the narrative
    # "belirlenen X TL"/label forms above — the confirmed root cause of
    # offering_price's near-zero (1/20) real extraction rate.
    text = "Bir payın nominal değeri 1,00 TL olup, 7,50 TL fiyattan satışa sunulacaktır."
    value, snippet = extract_offering_price(text)
    assert value == 7.50
    assert "7,50" in snippet


def test_extract_offering_price_par_value_sentence_no_fiyattan_suffix():
    # NETCD's real wording has no "fiyattan" at all — "TL'den satışa
    # sunulacaktır" directly.
    text = "Bir payın nominal değeri 1 TL olup, 46,00 TL'den satışa sunulacaktır."
    value, _ = extract_offering_price(text)
    assert value == 46.00


def test_extract_offering_price_par_value_sentence_repeats_par_value_first():
    # EMPAE's real wording repeats the par value as an adjective clause
    # between "olup" and the actual price — a gap that merely excludes
    # digits would stop at that repeated "1" and never reach 22,00.
    text = "Bir payın nominal değeri 1 TL olup 1 TL nominal değerli paylar, 22,00 TL fiyattan satışa sunulacaktır."
    value, _ = extract_offering_price(text)
    assert value == 22.00


def test_extract_offering_price_dilution_table_fallback():
    # The base prospectus's own "Sulanma Etkisi" (dilution effect) table
    # row — no "TL" unit token nearby, confirmed live against EKDMR's
    # real İzahname — used only when neither narrative form is found.
    text = "Sulanma Etkisi Analizi (TL) Halka Arz Öncesi Halka Arz Sonrası\nHalka Arz Fiyatı   45,00\nArtırılan Sermaye   40.000.000"
    value, _ = extract_offering_price(text)
    assert value == 45.00


def test_extract_par_value_per_share():
    text = "Bir payın nominal değeri 1,00 TL olup, 7,50 TL fiyattan satışa sunulacaktır."
    value, snippet = extract_par_value_per_share(text)
    assert value == 1.0


def test_extract_currency_returns_try_when_price_found():
    value, _ = extract_currency("belirlenen 76,60 TL")
    assert value == "TRY"


def test_extract_currency_not_found_when_no_price():
    assert extract_currency("fiyat bilgisi yok") is None


# --------------------------------------------------------------------------
# Distribution method — controlled vocabulary
# --------------------------------------------------------------------------


def test_extract_distribution_method_sabit_fiyat():
    text = "Halka arz Sabit Fiyatla Talep Toplama yöntemi ile gerçekleştirilecektir."
    value, snippet = extract_distribution_method(text)
    assert value == "Sabit Fiyatla Talep Toplama"


def test_extract_distribution_method_fiyat_araligi():
    text = "Pay satışı Fiyat Aralığı ile Talep Toplama yöntemiyle yapılacaktır."
    value, _ = extract_distribution_method(text)
    assert value == "Fiyat Aralığı ile Talep Toplama"


def test_extract_distribution_method_not_found():
    assert extract_distribution_method("belirsiz bir yöntemle satılacaktır") is None


def test_extract_distribution_method_sabit_fiyat_ile_variant():
    # Real shape (paraphrased from EKDMR's actual 2026 İzahname): "Sabit
    # Fiyat ile Talep Toplama" — a different real spacing/wording from
    # "Sabit Fiyatla Talep Toplama" above, which this exact phrase does
    # not match.
    text = "Halka arz satışı, 'Sabit Fiyat ile Talep Toplama' ve 'En İyi Gayret Aracılığı' yöntemi ile gerçekleştirilecektir."
    value, _ = extract_distribution_method(text)
    assert value == "Sabit Fiyat ile Talep Toplama"


# --------------------------------------------------------------------------
# Capital increase vs secondary sale
# --------------------------------------------------------------------------


def test_extract_capital_increase_shares_real_sentence():
    text = (
        "Ortaklığımızın çıkarılmış sermayesinin 1.400.000.000 TL'den 3.780.000.000 TL'ye "
        "çıkarılması nedeniyle artırılacak 2.380.000.000 TL nominal değerli paylarının halka arzı."
    )
    value, snippet = extract_capital_increase_shares(text)
    assert value == 2380000000.0
    assert "2.380.000.000" in snippet


def test_extract_capital_increase_ratio():
    text = "Şirketimizin sermayesi %170 oranında artırılarak 3.780.000.000 TL'ye çıkarılacaktır."
    value, _ = extract_capital_increase_ratio(text)
    assert value == 170.0


def test_extract_capital_increase_ratio_computed_from_before_after_capital_amounts():
    # Real shape (folded/paraphrased from ALBTN's actual 2026 investor
    # sale announcement): the ratio itself is never stated as a
    # percentage — only the absolute capital before/after the increase
    # — confirmed live across all 9 real 2026 announcements sampled.
    text = "Ortaklığımızın çıkarılmış sermayesinin 201.000.000 TL'den 250.000.000 TL'ye çıkarılması nedeniyle artırılacak 49.000.000 TL nominal değerli pay."
    value, snippet = extract_capital_increase_ratio(text)
    assert value == pytest.approx((250_000_000 - 201_000_000) / 201_000_000 * 100)
    assert "201.000.000" in snippet and "250.000.000" in snippet


def test_extract_capital_increase_ratio_explicit_percentage_wins_over_computed_amounts():
    # When a document states both forms, the direct percentage (rule 8's
    # own explicit-statement precedent) is preferred over computing one
    # from the before/after amounts — never overridden by a fallback.
    text = (
        "Şirketimizin sermayesi %170 oranında artırılarak 3.780.000.000 TL'ye çıkarılacaktır. "
        "Ortaklığımızın çıkarılmış sermayesinin 1.400.000.000 TL'den 3.780.000.000 TL'ye çıkarılması nedeniyle."
    )
    value, _ = extract_capital_increase_ratio(text)
    assert value == 170.0


def test_extract_secondary_sale_shares():
    # Real shape (paraphrased from a live 2026 investor sale announcement,
    # e.g. ATATR/MEYSU): "mevcut ortak(lar)... sahip olduğu"/"...ait",
    # never the literal "ortak satışı" phrase.
    text = (
        "Ortaklığımızın çıkarılmış sermayesinin 100.000.000 TL'den 130.000.000 TL'ye çıkarılması "
        "nedeniyle artırılacak 30.000.000 TL nominal değerli 30.000.000 adet pay ile mevcut ortak "
        "Örnek Holding A.Ş.'nin sahip olduğu 20.000.000 TL nominal değerli 20.000.000 adet pay "
        "olmak üzere toplam 50.000.000 TL nominal değerli 50.000.000 adet payın halka arzına ilişkin duyurudur."
    )
    value, snippet = extract_secondary_sale_shares(text)
    assert value == 20000000.0


def test_extract_secondary_sale_shares_multi_seller_returns_none():
    # A document naming several individual existing-shareholder sellers
    # (e.g. real EMPAE/EKDMR announcements) has no single stated total for
    # the secondary sale — never guessed/summed here; see
    # kap.offering_terms's total_offered − capital_increase fallback.
    text = (
        "Ortaklığımızın çıkarılmış sermayesinin 100.000.000 TL'den 130.000.000 TL'ye çıkarılması "
        "nedeniyle artırılacak 30.000.000 TL nominal değerli 30.000.000 adet pay, mevcut ortak "
        "Ada YILMAZ'a ait 12.000.000 TL nominal değerli 12.000.000 adet pay, mevcut ortak Deniz "
        "KAYA'ya ait 8.000.000 TL nominal değerli 8.000.000 adet pay olmak üzere toplam 50.000.000 "
        "TL nominal değerli 50.000.000 adet payın halka arzına ilişkin duyurudur."
    )
    assert extract_secondary_sale_shares(text) is None


def test_extract_capital_increase_shares_aggregated_secondary_sale_parenthetical():
    # Real shape (paraphrased from UCAYM's actual 2026 announcement): the
    # secondary sale is aggregated into the same clause via a
    # parenthetical, so "nominal değerli" no longer immediately follows
    # the capital-increase amount — _CAPITAL_INCREASE_RE alone would
    # never match this.
    text = (
        "Ortaklığımızın çıkarılmış sermayesinin 175.000.000 TL'den 225.000.000 TL'ye çıkarılması "
        "nedeniyle artırılacak 50.000.000 TL (ve mevcut ortakların sahip olduğu 10.000.000 TL "
        "olmak üzere toplam 60.000.000 TL) nominal değerli (B) grubu paylarının halka arzına ilişkin duyurudur."
    )
    value, _ = extract_capital_increase_shares(text)
    assert value == 50000000.0


def test_extract_total_offered_shares_tolerates_trailing_parenthesis():
    # Same UCAYM real shape: "...toplam 60.000.000 TL) nominal değerli"
    # — a stray ")" between the amount and "nominal değerli".
    text = (
        "Ortaklığımızın çıkarılmış sermayesinin 175.000.000 TL'den 225.000.000 TL'ye çıkarılması "
        "nedeniyle artırılacak 50.000.000 TL (ve mevcut ortakların sahip olduğu 10.000.000 TL "
        "olmak üzere toplam 60.000.000 TL) nominal değerli (B) grubu paylarının halka arzına ilişkin duyurudur."
    )
    value, _ = extract_total_offered_shares(text)
    assert value == 60000000.0


def test_extract_pre_and_post_offer_capital_from_announcement_sentence():
    text = "Ortaklığımızın çıkarılmış sermayesinin 100.000.000 TL'den 130.000.000 TL'ye çıkarılması nedeniyle artırılacak 30.000.000 TL nominal değerli pay."
    pre_value, _ = extract_pre_offer_capital(text)
    post_value, _ = extract_post_offer_capital(text)
    assert pre_value == 100000000.0
    assert post_value == 130000000.0


def test_extract_pre_and_post_offer_capital_dilution_table_fallback():
    # The base prospectus's own "Sulanma Etkisi" table restates the same
    # pre/post paid-in capital pair as "Ödenmiş Sermaye <pre> <post>" —
    # confirmed live against EKDMR's real İzahname, agreeing exactly
    # with its own announcement's narrative-sentence reading.
    text = "Ödenmiş Sermaye 280.000.000 320.000.000"
    pre_value, _ = extract_pre_offer_capital(text)
    post_value, _ = extract_post_offer_capital(text)
    assert pre_value == 280000000.0
    assert post_value == 320000000.0


def test_extract_secondary_sale_ratio():
    text = "Ortak satışı %15,5 oranında gerçekleştirilecektir."
    value, _ = extract_secondary_sale_ratio(text)
    assert value == 15.5


def test_extract_total_offered_shares():
    text = "Halka arz edilecek toplam 3.680.000.000 TL nominal değerli paylar satışa sunulmuştur."
    value, _ = extract_total_offered_shares(text)
    assert value == 3680000000.0


def test_capital_increase_and_secondary_sale_are_extracted_independently():
    """Same document mentioning both must not cross-contaminate values."""
    text = (
        "Ortaklığımızın çıkarılmış sermayesinin 100.000.000 TL'den 130.000.000 TL'ye çıkarılması "
        "nedeniyle artırılacak 30.000.000 TL nominal değerli 30.000.000 adet pay ile mevcut ortak "
        "Örnek Holding A.Ş.'nin sahip olduğu 20.000.000 TL nominal değerli 20.000.000 adet pay "
        "olmak üzere toplam 50.000.000 TL nominal değerli 50.000.000 adet payın halka arzına ilişkin duyurudur."
    )
    capital_value, _ = extract_capital_increase_shares(text)
    secondary_value, _ = extract_secondary_sale_shares(text)
    assert capital_value == 30000000.0
    assert secondary_value == 20000000.0


# --------------------------------------------------------------------------
# use_of_proceeds / key_risk_items — short structured lists
# --------------------------------------------------------------------------


def test_extract_use_of_proceeds_finds_heading_and_items():
    text = (
        "Fon Kullanım Yeri: Halka arzdan elde edilecek fonun tamamı üretim kapasitesinin "
        "artırılması amacıyla yeni makine alımında kullanılacaktır. Ayrıca işletme sermayesi "
        "ihtiyacının karşılanması için kullanılacaktır."
    )
    items = extract_use_of_proceeds(text)
    assert items is not None
    assert len(items) >= 1
    assert "üretim kapasitesinin" in items[0][0]


def test_extract_key_risk_items_finds_heading_and_items():
    text = (
        "Risk Faktörleri: Şirketin faaliyet gösterdiği sektördeki rekabetin artması "
        "gelirlerini olumsuz etkileyebilir. Döviz kurundaki dalgalanmalar maliyetleri artırabilir."
    )
    items = extract_key_risk_items(text)
    assert items is not None
    assert len(items) >= 1
    assert "rekabetin artması" in items[0][0]


def test_extract_use_of_proceeds_not_found_without_heading():
    assert extract_use_of_proceeds("bu belgede ilgili başlık yoktur") is None


def test_extract_key_risk_items_not_found_without_heading():
    assert extract_key_risk_items("bu belgede ilgili başlık yoktur") is None


# --------------------------------------------------------------------------
# Investor-group tahsisat (allocation) table
# --------------------------------------------------------------------------


def test_extract_investor_group_allocations_real_shape():
    # Real shape (paraphrased from EKDMR's actual 2026 İzahname §25.2.3(a)
    # "Yatırımcı grubu bazında tahsisat oranları").
    text = (
        "Halka arz edilecek toplam 52.000.000 TL nominal değerli payların; "
        "20.800.000 TL nominal değerdeki kısmı (40%) Yurt İçi Bireysel Yatırımcılara, "
        "5.200.000 TL nominal değerdeki kısmı (10%) Yüksek Talepte Bulunacak Yatırımcı Grubu'na, "
        "15.600.000 TL nominal değerdeki kısmı (30%) Yurt İçi Kurumsal Yatırımcılara, "
        "10.400.000 TL nominal değerdeki kısmı (20%) Yurt Dışı Kurumsal Yatırımcılara "
        "gerçekleştirilecek satışlar için tahsis edilmiştir."
    )
    items = extract_investor_group_allocations(text)
    assert items is not None
    groups = [item.group for item, _ in items]
    assert groups == ["retail", "high_demand", "domestic_institutional", "foreign_institutional"]

    retail = items[0][0]
    assert retail.amount_try == 20800000.0
    assert retail.percentage == 40.0

    total = sum(item.amount_try for item, _ in items)
    assert total == 52000000.0
    total_pct = sum(item.percentage for item, _ in items)
    assert total_pct == 100.0


def test_extract_investor_group_allocations_not_found_without_table():
    assert extract_investor_group_allocations("bu belgede tahsisat tablosu yoktur") is None


# --------------------------------------------------------------------------
# Provenance: extract_observations_from_pages
# --------------------------------------------------------------------------


def test_extract_observations_records_page_number_and_source():
    page1 = PdfPage(number=1, text="giriş bölümü, fiyat bilgisi yok")
    page2 = PdfPage(number=2, text="belirlenen 76,60 TL olarak açıklanmıştır")

    observations = extract_observations_from_pages(
        [page1, page2],
        document_type="investor_sale_announcement",
        disclosure_id="disc-123",
        attachment_url="https://example/doc.pdf",
    )

    obs = observations["offering_price"]
    assert obs.value == 76.60
    assert obs.source.page_number == 2
    assert obs.source.disclosure_id == "disc-123"
    assert obs.source.attachment_url == "https://example/doc.pdf"
    assert obs.source.document_type == "investor_sale_announcement"
    assert "76,60" in obs.raw_snippet


def test_extract_observations_takes_first_match_across_pages():
    page1 = PdfPage(number=1, text="belirlenen 50,00 TL")
    page2 = PdfPage(number=2, text="belirlenen 99,00 TL")

    observations = extract_observations_from_pages(
        [page1, page2], document_type="approved_prospectus", disclosure_id="d1", attachment_url="url"
    )
    assert observations["offering_price"].value == 50.00
    assert observations["offering_price"].source.page_number == 1


def test_extract_observations_missing_field_absent_from_dict():
    page = PdfPage(number=1, text="alakasız bir metin, hiçbir alan yok")
    observations = extract_observations_from_pages(
        [page], document_type="approved_prospectus", disclosure_id="d1", attachment_url="url"
    )
    assert "offering_price" not in observations


# --------------------------------------------------------------------------
# merge_field_observations: missing / single / agreeing / conflicting
# --------------------------------------------------------------------------

SRC_P = SourceRef("approved_prospectus", "disc-p", "url-p", 1)
SRC_A = SourceRef("investor_sale_announcement", "disc-a", "url-a", 2)


def test_merge_neither_present_is_not_found():
    fact = merge_field_observations("offering_price", None, None)
    assert fact.status == "not_found"
    assert fact.value is None
    assert fact.observations == ()


def test_merge_only_prospectus_present():
    obs = FieldObservation(value=100.0, raw_snippet="snip", source=SRC_P)
    fact = merge_field_observations("capital_increase_shares", obs, None)
    assert fact.status == "extracted"
    assert fact.value == 100.0
    assert fact.source == SRC_P
    assert fact.observations == (obs,)


def test_merge_only_announcement_present():
    obs = FieldObservation(value=76.6, raw_snippet="snip", source=SRC_A)
    fact = merge_field_observations("offering_price", None, obs)
    assert fact.status == "extracted"
    assert fact.source == SRC_A


def test_merge_agreeing_values_prefers_announcement_for_price_fields():
    obs_p = FieldObservation(value=76.6, raw_snippet="p", source=SRC_P)
    obs_a = FieldObservation(value=76.6, raw_snippet="a", source=SRC_A)
    fact = merge_field_observations("offering_price", obs_p, obs_a)
    assert fact.status == "extracted"
    assert fact.value == 76.6
    assert fact.source.document_type == "investor_sale_announcement"
    assert len(fact.observations) == 2  # both kept for provenance


def test_merge_agreeing_values_prefers_prospectus_for_structure_fields():
    obs_p = FieldObservation(value=2000.0, raw_snippet="p", source=SRC_P)
    obs_a = FieldObservation(value=2000.0, raw_snippet="a", source=SRC_A)
    fact = merge_field_observations("capital_increase_shares", obs_p, obs_a)
    assert fact.source.document_type == "approved_prospectus"


def test_merge_conflicting_values_does_not_silently_pick_one():
    obs_p = FieldObservation(value=76.6, raw_snippet="p", source=SRC_P)
    obs_a = FieldObservation(value=80.0, raw_snippet="a", source=SRC_A)
    fact = merge_field_observations("offering_price", obs_p, obs_a)
    assert fact.status == "conflicting"
    assert fact.value is None
    assert fact.source is None
    assert set(fact.observations) == {obs_p, obs_a}


# --------------------------------------------------------------------------
# build_extracted_facts: full field set
# --------------------------------------------------------------------------


def test_build_extracted_facts_covers_every_field():
    facts = build_extracted_facts(None, None)
    assert set(facts.as_dict().keys()) == set(FIELD_NAMES)
    assert all(f.status == "not_found" for f in facts.as_dict().values())


def test_build_extracted_facts_merges_both_sides():
    prospectus_obs = {"capital_increase_shares": FieldObservation(2000.0, "p-snip", SRC_P)}
    announcement_obs = {"offering_price": FieldObservation(76.6, "a-snip", SRC_A)}
    facts = build_extracted_facts(prospectus_obs, announcement_obs)

    assert facts.capital_increase_shares.status == "extracted"
    assert facts.capital_increase_shares.value == 2000.0
    assert facts.offering_price.status == "extracted"
    assert facts.offering_price.value == 76.6
    assert facts.subscription_start_date.status == "not_found"


# --------------------------------------------------------------------------
# IPO results fields (post-offer) — one success, one missing, one
# conflicting, one provenance-preservation check.
# --------------------------------------------------------------------------

_IPO_RESULTS_TEXT = (
    "Halka arzda, halka arz edilen 85.000.000 TL nominal değerli payların 4,98 katına denk gelen "
    "422.947.704 TL nominal değerli filtrelenmemiş pay talebi gelmiştir. Yurt İçi Bireysel "
    "Yatırımcılara tahsisat tutarının yaklaşık 1,48 katı talep gelmiştir.\n"
    "Yurt İçi Bireysel \nYatırımcılar 68.000.000 80,0% 1.120.538 100.300.410 23.7% 1.089.645 68.000.000 80,0%"
)


def test_ipo_results_extraction_succeeds_for_a_real_narrative_sentence():
    value, snippet = extract_total_demand_multiple(_IPO_RESULTS_TEXT)
    assert value == 4.98
    assert "katına denk gelen" in snippet


def test_ipo_results_field_is_missing_when_not_stated():
    assert extract_retail_allocated_shares("bu belgede dağıtım bilgisi yoktur") is None


def test_ipo_results_conflicting_observation_is_not_silently_picked():
    # An original vs. an amended/corrected results filing disagreeing on
    # the same field — merge_field_observations must not silently prefer
    # one; both observations stay attached for provenance.
    src_original = SourceRef("ipo_results", "disc-original", "url-1", 1)
    src_amended = SourceRef("ipo_results", "disc-amended", "url-2", 1)
    original = FieldObservation(value=1.48, raw_snippet="1,48 katı", source=src_original)
    amended = FieldObservation(value=1.55, raw_snippet="1,55 katı", source=src_amended)

    fact = merge_field_observations("retail_demand_multiple", None, original, amended)

    assert fact.status == "conflicting"
    assert fact.value is None
    assert set(fact.observations) == {original, amended}


def test_ipo_results_provenance_is_preserved():
    page = PdfPage(number=1, text=_IPO_RESULTS_TEXT)
    observations = extract_observations_from_pages(
        [page], document_type="ipo_results", disclosure_id="d-results", attachment_url="https://example/results.pdf"
    )
    source = observations["total_demand_multiple"].source
    assert source.document_type == "ipo_results"
    assert source.disclosure_id == "d-results"
    assert source.attachment_url == "https://example/results.pdf"
    assert source.page_number == 1
    assert source.extraction_method == "digital"


# --------------------------------------------------------------------------
# Price determination report fields (valuation summary) — one success,
# one missing, one conflicting, one provenance-preservation check.
#
# Text below is a verbatim excerpt of the "Değerleme Özeti"/"Değer
# Çarpanları" summary tables (page 8) of a real Fiyat Tespit Raporu,
# confirmed live against the cached PDF before writing the extractors.
# --------------------------------------------------------------------------

_PRICE_DETERMINATION_REPORT_TEXT = (
    "Değerleme Özeti Değer (m$) Ağırlık Pay Başı Değer (TL)\n"
    "Metodolojiler\n"
    "İNA 338,8 50,0% 28,69\n"
    "Yurtdışı Benzerler 316,4 25,0% 26,79\n"
    "BİST Benzer 188,1 25,0% 15,93\n"
    "Halka Arz Piyasa Değeri 295,5 100% 25,02\n"
    "Halka Arz İskontosu -20%\n"
    "Nihai Değer 236,2\n"
    "13.05.2026 Tarihli USD/TL 46,10\n"
    "Piyasa Değeri (mTL) 10.888 20,00\n"
    "Değer Çarpanları 2026/03 4Ç 2026T 2027T\n"
    "EV/EBITDA 11,5 12,1 9,1\n"
    "F/K 29,4 m.d. m.d.\n"
    "EV/Net Satış 8,1 8,2 6,6\n"
    "PD/DD 0,8 m.d. m.d.\n"
)


def test_price_determination_report_extraction_succeeds_for_a_real_summary_line():
    value, snippet = extract_reported_ev_ebitda(_PRICE_DETERMINATION_REPORT_TEXT)
    assert value == 11.5
    assert "EV/EBITDA" in snippet


def test_price_determination_report_field_is_missing_when_not_stated():
    # The report only ever states "EV/Net Satış" (EV/Sales) — a P/S
    # extractor must not be confused by the similar-looking label.
    assert extract_reported_ps(_PRICE_DETERMINATION_REPORT_TEXT) is None


def test_price_determination_report_conflicting_observation_is_not_silently_picked():
    # Two disagreeing price-determination-report observations of the same
    # field (e.g. an original vs. an amended report) must not be silently
    # resolved — both are kept for provenance.
    src_original = SourceRef("price_determination_report", "disc-original", "url-1", 8)
    src_amended = SourceRef("price_determination_report", "disc-amended", "url-2", 8)
    original = FieldObservation(value=20.0, raw_snippet="İskontosu -20%", source=src_original)
    amended = FieldObservation(value=25.03, raw_snippet="%25,03 iskontoludur", source=src_amended)

    fact = merge_field_observations(
        "headline_discount_percentage", None, None, original, amended
    )

    assert fact.status == "conflicting"
    assert fact.value is None
    assert set(fact.observations) == {original, amended}


def test_price_determination_report_provenance_is_preserved():
    page = PdfPage(number=8, text=_PRICE_DETERMINATION_REPORT_TEXT)
    observations = extract_observations_from_pages(
        [page],
        document_type="price_determination_report",
        disclosure_id="d-report",
        attachment_url="https://example/report.pdf",
    )
    source = observations["reported_ev_ebitda"].source
    assert source.document_type == "price_determination_report"
    assert source.disclosure_id == "d-report"
    assert source.attachment_url == "https://example/report.pdf"
    assert source.page_number == 8
    assert source.extraction_method == "digital"
