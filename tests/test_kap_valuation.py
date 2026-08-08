from datetime import date, datetime

from halka_arz_advisor.kap.extraction import SourceRef
from halka_arz_advisor.kap.financials import FinancialObservation
from halka_arz_advisor.kap.manual_confirmation import ManualFieldConfirmation, complete_offering_terms
from halka_arz_advisor.kap.offering_terms import OFFERING_TERM_FIELD_NAMES, OfferingTerms, OfferingTermField
from halka_arz_advisor.kap.valuation import build_valuation_evidence

PDR_SOURCE = SourceRef("price_determination_report", "d-pdr", "url", 5)
IPO_RESULTS_SOURCE = SourceRef("ipo_results", "d-results", "url", 1)


def _field(status="not_found", value=None, unit=None):
    return OfferingTermField(status=status, value=value, unit=unit, derived=False, observations=())


def _terms(**overrides) -> OfferingTerms:
    base = {name: _field() for name in OFFERING_TERM_FIELD_NAMES}
    base.update(overrides)
    return OfferingTerms(**base)


def _terms_with_market_cap(value=5_000_000_000.0) -> OfferingTerms:
    return _terms(implied_post_money_market_cap=_field("extracted", value, "TRY"))


def _obs(metric_name, value, *, currency="TRY", scale="thousand", period_type="ANNUAL", period_end=date(2024, 12, 31), source=PDR_SOURCE):
    return FinancialObservation(
        metric_name, value, currency, scale, date(2024, 1, 1), period_end, period_type, "consolidated", None, "x", source,
    )


def test_full_set_of_multiples_computed_from_real_shaped_financials():
    terms = _terms_with_market_cap(5_000_000_000.0)
    completed = complete_offering_terms(terms)
    observations = (
        _obs("net_income", 250_000.0),  # 250,000 thousand TRY = 250,000,000 TRY
        _obs("revenue", 3_000_000.0),  # 3,000,000,000 TRY
        _obs("equity", 1_200_000.0),  # 1,200,000,000 TRY
    )

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.implied_market_cap.value == 5_000_000_000.0
    assert evidence.pe_at_offer.value == 20.0
    assert evidence.ps_at_offer.value == 5_000_000_000.0 / 3_000_000_000.0
    assert evidence.pb_at_offer.value == 5_000_000_000.0 / 1_200_000_000.0
    assert evidence.pe_at_offer.period_end == date(2024, 12, 31)
    assert evidence.ev_ebitda_at_offer.status == "unavailable"
    assert evidence.sufficiency == "SUFFICIENT"


def test_no_market_cap_means_insufficient_regardless_of_financials():
    terms = _terms()  # implied_post_money_market_cap not_found
    completed = complete_offering_terms(terms)
    observations = (_obs("net_income", 250_000.0),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.implied_market_cap.status == "unavailable"
    assert "not_found" in evidence.implied_market_cap.unavailable_reason
    assert evidence.pe_at_offer.status == "unavailable"
    assert evidence.sufficiency == "INSUFFICIENT"
    assert "no valuation anchor" in evidence.sufficiency_reason


def test_conflicting_market_cap_is_never_arbitrated():
    terms = _terms(implied_post_money_market_cap=_field("conflicting", None, "TRY"))
    completed = complete_offering_terms(terms)
    observations = (_obs("net_income", 250_000.0),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.implied_market_cap.status == "unavailable"
    assert "conflicting" in evidence.implied_market_cap.unavailable_reason
    assert evidence.pe_at_offer.status == "unavailable"


def test_currency_mismatch_is_never_bridged():
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)
    observations = (_obs("net_income", 10.0, currency="USD", scale="million"),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.pe_at_offer.status == "unavailable"
    assert "TRY" in evidence.pe_at_offer.unavailable_reason


def test_interim_period_is_never_used_for_a_multiple():
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)
    observations = (_obs("net_income", 250_000.0, period_type="INTERIM"),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.pe_at_offer.status == "unavailable"


def test_unrecognized_scale_is_never_guessed():
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)
    observations = (_obs("net_income", 250.0, scale="billion"),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.pe_at_offer.status == "unavailable"
    assert "billion" in evidence.pe_at_offer.unavailable_reason


def test_negative_net_income_blocks_pe_as_not_meaningful():
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)
    observations = (_obs("net_income", -50_000.0),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.pe_at_offer.status == "unavailable"
    assert "zero or negative" in evidence.pe_at_offer.unavailable_reason


def test_post_offer_document_observations_are_never_used_even_if_passed_in():
    """Leakage safety: a caller mistakenly including a post-offer
    ipo_results-sourced financial observation must never influence the
    pre-offer valuation — this module re-filters by document type
    itself, never trusting the caller's own filtering."""
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)
    observations = (_obs("net_income", 250_000.0, source=IPO_RESULTS_SOURCE),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.pe_at_offer.status == "unavailable"


def test_insurance_sector_marks_ps_not_applicable_not_unavailable():
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)
    observations = (_obs("revenue", 3_000_000.0),)

    evidence = build_valuation_evidence(terms, completed, observations, sector="insurance")

    assert evidence.ps_at_offer.status == "not_applicable"


def test_manual_confirmation_of_market_cap_unblocks_valuation():
    terms = _terms()  # not_found
    confirmation = ManualFieldConfirmation("implied_post_money_market_cap", 5_000_000_000.0, "burak", datetime(2026, 8, 8))
    completed = complete_offering_terms(terms, [confirmation])
    observations = (_obs("net_income", 250_000.0),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.implied_market_cap.status == "computed"
    assert evidence.implied_market_cap.value == 5_000_000_000.0
    assert evidence.pe_at_offer.status == "computed"
    assert evidence.sufficiency == "SUFFICIENT"


def test_partial_sufficiency_from_one_multiple_is_enough():
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)
    # Only equity resolves (P/B) — no net_income or revenue.
    observations = (_obs("equity", 1_200_000.0),)

    evidence = build_valuation_evidence(terms, completed, observations)

    assert evidence.pb_at_offer.status == "computed"
    assert evidence.pe_at_offer.status == "unavailable"
    assert evidence.ps_at_offer.status == "unavailable"
    assert evidence.sufficiency == "SUFFICIENT"


def test_ev_ebitda_is_always_unavailable_and_explicit_about_why():
    terms = _terms_with_market_cap()
    completed = complete_offering_terms(terms)

    evidence = build_valuation_evidence(terms, completed, ())

    assert evidence.ev_ebitda_at_offer.status == "unavailable"
    assert "depreciation" in evidence.ev_ebitda_at_offer.unavailable_reason
