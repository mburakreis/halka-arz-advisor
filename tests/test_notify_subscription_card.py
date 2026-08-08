from datetime import date, datetime

from halka_arz_advisor.decision.subscription_v1 import SubscriptionDecisionInputs, evaluate_subscription_decision
from halka_arz_advisor.ipo_outcomes.models import IpoMarketOutcome
from halka_arz_advisor.kap.extraction import SourceRef
from halka_arz_advisor.kap.financials import FinancialObservation
from halka_arz_advisor.kap.manual_confirmation import ManualFieldConfirmation, complete_offering_terms
from halka_arz_advisor.kap.offering_terms import OFFERING_TERM_FIELD_NAMES, OfferingTerms, OfferingTermField, OfferingTermObservation
from halka_arz_advisor.kap.valuation import build_valuation_evidence
from halka_arz_advisor.notify.subscription_card import MAX_MESSAGE_CHARS, format_subscription_card

AS_OF = datetime(2026, 8, 10)


def _field(status="not_found", value=None, unit=None, observations=()):
    return OfferingTermField(status=status, value=value, unit=unit, derived=False, observations=observations)


def _terms(**overrides) -> OfferingTerms:
    base = {name: _field() for name in OFFERING_TERM_FIELD_NAMES}
    base.update(overrides)
    return OfferingTerms(**base)


def _resolved_terms(**overrides) -> OfferingTerms:
    base = dict(
        offer_price=_field("extracted", 12.5, "TRY"),
        subscription_start=_field("extracted", date(2026, 8, 1), "date"),
        subscription_end=_field("extracted", date(2026, 8, 20), "date"),
        distribution_method=_field("extracted", "sabit fiyatla talep toplama"),
        total_offered_shares=_field("extracted", 1_000_000.0, "shares"),
        new_issue_shares=_field("extracted", 1_000_000.0, "shares"),
        retail_distribution_rule=_field("extracted", "equal"),
        retail_allocation_percentage=_field("extracted", 40.0, "percent"),
        retail_offered_shares=_field("extracted", 400_000.0, "shares"),
        implied_post_money_market_cap=_field("extracted", 5_000_000_000.0, "TRY"),
    )
    base.update(overrides)
    return _terms(**base)


def _outcome(ticker: str, trading_start: date, bist_relative_5d: float) -> IpoMarketOutcome:
    return IpoMarketOutcome(
        ticker=ticker, company_name=None, offer_price=10.0,
        resolved_trading_start_date=trading_start, spk_trading_start_date=trading_start,
        kap_trading_start_announcement_dates=(), trading_start_conflict=False,
        price_observation_count=10, last_price_observation_date=trading_start,
        first_day_return=5.0, return_5d=8.0, return_20d=None, return_3m=None,
        max_drawdown_5d=None, max_drawdown_20d=None, max_drawdown_3m=None,
        bist_relative_first_day=5.0, bist_relative_5d=bist_relative_5d, bist_relative_20d=None, bist_relative_3m=None,
        generated_at=datetime(2026, 7, 1),
    )


FAVORABLE_OUTCOMES = tuple(_outcome(f"F{i}", date(2026, 7, 15), 25.0) for i in range(5))

_PDR_SOURCE = SourceRef("price_determination_report", "d-pdr", "url", 5)
SUFFICIENT_FINANCIAL_OBSERVATIONS = (
    FinancialObservation(
        "net_income", 500_000.0, "TRY", "thousand", date(2024, 1, 1), date(2024, 12, 31), "ANNUAL", "consolidated", None,
        "x", _PDR_SOURCE,
    ),
)


def _decision(terms, **kwargs):
    completed = complete_offering_terms(terms, kwargs.pop("confirmations", ()))
    valuation = build_valuation_evidence(terms, completed, kwargs.pop("financial_observations", ()))
    inputs = SubscriptionDecisionInputs(
        offering_terms=terms, completed_terms=completed, derived_financials=kwargs.pop("derived", None),
        valuation_evidence=valuation, market_context=kwargs.pop("market", None), as_of=AS_OF, ticker="ORNK",
        recent_ipo_outcomes=kwargs.pop("recent_ipo_outcomes", ()), disclosures=kwargs.pop("disclosures", ()),
    )
    return completed, evaluate_subscription_decision(inputs)


def test_cannot_assess_card_shows_exactly_what_needs_manual_confirmation_not_a_fake_recommendation():
    terms = _terms()  # nothing resolved
    completed, decision = _decision(terms)

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert "Değerlendirilemiyor" in message
    assert "elle onaylanması gerekiyor" in message
    assert "Katıl (kısa vadeli işlem)" not in message
    assert "Katıl (tutma seçeneğiyle)" not in message


def test_resolved_card_with_favorable_regime_shows_subscribe_and_regime_stats():
    terms = _resolved_terms()
    completed, decision = _decision(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES)

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert "Örnek A.Ş. (ORNK)" in message
    assert "12.50 TL" in message
    assert "01.08.2026 - 20.08.2026" in message
    assert "sabit fiyatla talep toplama" in message
    assert "Tahsisat senaryoları" in message
    assert "50.000 katılımcı" in message
    assert "Katıl (kısa vadeli işlem)" in message
    assert "Yakın dönem halka arz rejimi" in message
    assert "Olgun karşılaştırma sayısı: 5" in message


def test_supportive_mechanics_alone_shows_watch_not_subscribe():
    terms = _resolved_terms()
    completed, decision = _decision(terms, recent_ipo_outcomes=())  # no regime evidence

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert decision.action == "WATCH_SUBSCRIPTION"
    assert "İzle — henüz net bir avantaj yok" in message
    assert "Katıl (kısa vadeli işlem)" not in message


def test_manually_confirmed_field_is_visibly_marked_in_the_card():
    terms = _resolved_terms(offer_price=_field("not_found"))
    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8))
    completed, decision = _decision(terms, confirmations=[confirmation], recent_ipo_outcomes=FAVORABLE_OUTCOMES)

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert "Elle onaylanan alanlar" in message
    assert "burak" in message
    assert "Fiyat: 12.50 TL" in message
    assert "Fiyat: Bilinmiyor" not in message
    assert "offer_price" in decision.manually_confirmed_fields


def test_market_context_is_shown_as_context_separately_from_the_regime_evidence():
    from halka_arz_advisor.evds.models import MarketContextFeatureValue, MarketContextSnapshot

    terms = _resolved_terms()
    market = MarketContextSnapshot(
        features={"bist100_return_20d": MarketContextFeatureValue(-12.3, date(2026, 8, 8), ("x",))}
    )
    completed, decision = _decision(terms, market=market, recent_ipo_outcomes=FAVORABLE_OUTCOMES)

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
        market_context=market,
    )

    assert "Piyasa rejimi (bağlam amaçlı, karara dahil edilmez)" in message
    assert "Yakın dönem halka arz rejimi" in message
    assert "Katıl (kısa vadeli işlem)" in message


def test_source_links_are_included_when_available():
    obs = _field(
        "extracted", 12.5, "TRY",
        observations=(
            OfferingTermObservation(
                value=12.5, raw_snippet="x", source_document_type="investor_sale_announcement",
                source_disclosure_id="d1", source_published_at=None, source_page_number=1,
                extraction_method="digital", source_system="kap",
            ),
        ),
    )
    terms = _resolved_terms(offer_price=obs)
    completed, decision = _decision(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES)

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
        disclosure_notification_urls={"d1": "https://www.kap.org.tr/tr/Bildirim/1"},
    )

    assert "Kaynaklar" in message
    assert "https://www.kap.org.tr/tr/Bildirim/1" in message


def test_allocation_scenarios_are_labeled_as_not_a_forecast():
    terms = _resolved_terms()
    completed, decision = _decision(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES)

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert "bir talep tahmini değildir" in message


def test_valuation_section_shows_computed_multiples_and_is_never_a_cheap_expensive_verdict():
    terms = _resolved_terms()
    completed, decision = _decision(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES, financial_observations=SUFFICIENT_FINANCIAL_OBSERVATIONS)

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert "Değerleme" in message
    assert "F/K:" in message and "bilinmiyor" not in message.split("F/K:")[1][:20]
    assert "5.000.000.000 TL" in message
    assert "ucuz/pahalı hükmü değildir" in message
    assert "ucuz" not in message.replace("ucuz/pahalı hükmü değildir", "")
    assert "pahalı" not in message.replace("ucuz/pahalı hükmü değildir", "")


def test_valuation_section_shows_why_multiples_are_unavailable_when_no_financials():
    terms = _resolved_terms()
    completed, decision = _decision(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES)  # no financial_observations

    message = format_subscription_card(
        company_name="Örnek A.Ş.", ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert "F/K: bilinmiyor" in message
    assert "Fiyat sağlaması için kanıt: Yetersiz" in message


def test_card_stays_within_the_telegram_message_length_budget():
    terms = _resolved_terms()
    completed, decision = _decision(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES)

    message = format_subscription_card(
        company_name="Çok Uzun Bir Şirket Adı A.Ş." * 5, ticker="ORNK", offering_terms=terms, completed_terms=completed, decision=decision,
    )

    assert len(message) <= MAX_MESSAGE_CHARS
