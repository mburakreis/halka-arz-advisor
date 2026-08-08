from datetime import date, datetime

from halka_arz_advisor.decision.subscription_v1 import (
    SubscriptionDecisionInputs,
    evaluate_subscription_decision,
)
from halka_arz_advisor.ipo_outcomes.models import IpoMarketOutcome
from halka_arz_advisor.kap.derived_financials import DERIVED_FINANCIAL_FEATURE_NAMES, DerivedFinancialFeature, DerivedFinancialFeatures
from halka_arz_advisor.kap.extraction import SourceRef
from halka_arz_advisor.kap.financials import FinancialObservation
from halka_arz_advisor.kap.manual_confirmation import ManualFieldConfirmation, complete_offering_terms
from halka_arz_advisor.kap.models import KapDisclosure
from halka_arz_advisor.kap.offering_terms import OFFERING_TERM_FIELD_NAMES, OfferingTerms, OfferingTermField
from halka_arz_advisor.kap.valuation import build_valuation_evidence

AS_OF = datetime(2026, 8, 10)

_PDR_SOURCE = SourceRef("price_determination_report", "d-pdr", "url", 5)
# A real-shaped ANNUAL TRY net_income observation, compatible with
# _resolved_terms()'s own implied_post_money_market_cap — enough on its
# own to make build_valuation_evidence report SUFFICIENT (pe_at_offer
# computed), for tests that need a resolved valuation anchor.
SUFFICIENT_FINANCIAL_OBSERVATIONS = (
    FinancialObservation(
        "net_income", 500_000.0, "TRY", "thousand", date(2024, 1, 1), date(2024, 12, 31), "ANNUAL", "consolidated", None,
        "x", _PDR_SOURCE,
    ),
)


def _field(status="not_found", value=None, unit=None):
    return OfferingTermField(status=status, value=value, unit=unit, derived=False, observations=())


def _terms(**overrides) -> OfferingTerms:
    base = {name: _field() for name in OFFERING_TERM_FIELD_NAMES}
    base.update(overrides)
    return OfferingTerms(**base)


def _resolved_terms(**overrides) -> OfferingTerms:
    """A fully critical-evidence-resolved OfferingTerms: equal
    distribution, a substantial (40%) retail tranche (mechanics
    SUPPORTIVE), an open subscription window, and a consistent share
    equation — the baseline every test below starts from and tweaks."""
    base = dict(
        offer_price=_field("extracted", 12.5, "TRY"),
        subscription_start=_field("extracted", date(2026, 8, 1), "date"),
        subscription_end=_field("extracted", date(2026, 8, 20), "date"),
        distribution_method=_field("extracted", "sabit fiyatla talep toplama"),
        total_offered_shares=_field("extracted", 1_000_000.0, "shares"),
        new_issue_shares=_field("extracted", 1_000_000.0, "shares"),
        secondary_sale_shares=_field("not_found"),
        retail_distribution_rule=_field("extracted", "equal"),
        retail_allocation_percentage=_field("extracted", 40.0, "percent"),
        retail_offered_shares=_field("extracted", 400_000.0, "shares"),
        implied_post_money_market_cap=_field("extracted", 10_000_000_000.0, "TRY"),
    )
    base.update(overrides)
    return _terms(**base)


def _derived_feature(name: str, status="unavailable", value=None) -> DerivedFinancialFeature:
    return DerivedFinancialFeature(
        feature_name=name, status=status, value=value, unavailable_reason=None if status == "computed" else "no data",
        input_observation_ids=(), source_fact_ids=(), formula_version="1",
    )


def _derived(**overrides) -> DerivedFinancialFeatures:
    base = {name: _derived_feature(name) for name in DERIVED_FINANCIAL_FEATURE_NAMES}
    base.update(overrides)
    return DerivedFinancialFeatures(**base)


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


# A favorable comparable-IPO pool: 5 mature IPOs, all with a strongly
# positive 5-day BIST-relative return, well inside the 90-day lookback
# and fully realized before AS_OF.
FAVORABLE_OUTCOMES = tuple(_outcome(f"F{i}", date(2026, 7, 15), 25.0) for i in range(5))
UNFAVORABLE_OUTCOMES = tuple(_outcome(f"U{i}", date(2026, 7, 15), -20.0) for i in range(5))


def _inputs(
    terms: OfferingTerms, *, derived=None, market=None, as_of=AS_OF, disclosures=(), confirmations=(),
    ticker="ORNK", recent_ipo_outcomes=(), financial_observations=(),
) -> SubscriptionDecisionInputs:
    completed = complete_offering_terms(terms, confirmations)
    # Built via the real kap.valuation function (not a hand-rolled
    # stand-in), so these tests also exercise the actual valuation
    # module end-to-end, not just subscription_v1's own plumbing.
    valuation = build_valuation_evidence(terms, completed, financial_observations)
    return SubscriptionDecisionInputs(
        offering_terms=terms, completed_terms=completed, derived_financials=derived,
        valuation_evidence=valuation, market_context=market, as_of=as_of, ticker=ticker,
        recent_ipo_outcomes=recent_ipo_outcomes, disclosures=disclosures,
    )


def test_missing_critical_field_forces_cannot_assess_not_pass():
    terms = _resolved_terms(offer_price=_field("not_found"))

    result = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES))

    assert result.action == "CANNOT_ASSESS_SUBSCRIPTION"
    assert "offer_price" in result.missing_critical_evidence
    assert result.subscription_evidence_grade == "NONE"
    assert result.action != "PASS_SUBSCRIPTION"


def test_conflicting_critical_field_also_forces_cannot_assess():
    terms = _resolved_terms(distribution_method=_field("conflicting"))

    result = evaluate_subscription_decision(_inputs(terms))

    assert result.action == "CANNOT_ASSESS_SUBSCRIPTION"
    assert "distribution_method (conflicting)" in result.missing_critical_evidence


def test_official_conflict_not_overridden_by_manual_input():
    terms = _resolved_terms(offer_price=_field("conflicting", None, "TRY"))
    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8))

    result = evaluate_subscription_decision(_inputs(terms, confirmations=[confirmation]))

    assert result.action == "CANNOT_ASSESS_SUBSCRIPTION"
    assert "offer_price (conflicting)" in result.missing_critical_evidence


def test_manual_confirmation_unblocks_an_otherwise_cannot_assess_decision_and_is_flagged_as_manual():
    terms = _resolved_terms(offer_price=_field("not_found"))
    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8))

    result = evaluate_subscription_decision(_inputs(terms, confirmations=[confirmation], recent_ipo_outcomes=FAVORABLE_OUTCOMES))

    assert result.action != "CANNOT_ASSESS_SUBSCRIPTION"
    assert not result.missing_critical_evidence
    # Manual-confirmed critical terms remain visibly identified as
    # manual, not indistinguishable from official extraction.
    assert "offer_price" in result.manually_confirmed_fields


# --------------------------------------------------------------------
# 1. Mechanics vs. subscription edge must be fully decoupled.
# --------------------------------------------------------------------


def test_supportive_mechanics_with_unknown_regime_does_not_subscribe():
    """The old (r1) bug: equal distribution + a 40% retail tranche must
    no longer manufacture a positive subscription edge or action on
    their own."""
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=()))

    assert result.mechanics_state == "SUPPORTIVE"
    assert result.subscription_edge == "UNKNOWN"
    assert result.action == "WATCH_SUBSCRIPTION"
    assert result.action not in ("SUBSCRIBE_FOR_LISTING_TRADE", "SUBSCRIBE_WITH_HOLD_OPTION")


def test_supportive_mechanics_with_favorable_regime_subscribes():
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES))

    assert result.mechanics_state == "SUPPORTIVE"
    assert result.subscription_edge == "FAVORABLE"
    assert result.action == "SUBSCRIBE_FOR_LISTING_TRADE"
    assert result.intended_horizon == "5D_LISTING_TRADE"


def test_unfavorable_regime_blocks_subscribe_regardless_of_mechanics():
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=UNFAVORABLE_OUTCOMES))

    assert result.subscription_edge == "UNFAVORABLE"
    assert result.action == "PASS_SUBSCRIPTION"


def test_proportional_distribution_is_not_automatically_unfavorable():
    """Proportional mechanics alone (no thin tranche) must classify as
    NEUTRAL mechanics, not CONSTRAINED — harder-to-estimate lots is not
    itself a red flag."""
    terms = _resolved_terms(retail_distribution_rule=_field("extracted", "proportional"))

    result = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES))

    assert result.mechanics_state == "NEUTRAL"
    # With a genuinely favorable regime and non-prohibitive mechanics,
    # this can still subscribe.
    assert result.action == "SUBSCRIBE_FOR_LISTING_TRADE"


def test_thin_retail_tranche_is_constrained_mechanics_and_blocks_subscribe():
    terms = _resolved_terms(retail_allocation_percentage=_field("extracted", 3.0, "percent"))

    result = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES))

    assert result.mechanics_state == "CONSTRAINED"
    # Constrained mechanics blocks a positive action even under a
    # favorable regime, but is not treated as a hard "clearly
    # unfavorable" blocker either.
    assert result.action == "WATCH_SUBSCRIPTION"


def test_no_look_ahead_from_the_target_ipos_own_outcome():
    terms = _resolved_terms()
    # A pool containing only the target's own (spectacular) outcome —
    # since exclude_ticker matches, this must resolve to UNKNOWN
    # regime, not a favorable one based on the target's own result.
    own_outcome = _outcome("ORNK", date(2026, 7, 15), 500.0)

    result = evaluate_subscription_decision(_inputs(terms, ticker="ORNK", recent_ipo_outcomes=(own_outcome,)))

    assert result.recent_ipo_regime.mature_ipo_count == 0
    assert "ORNK" not in result.recent_ipo_regime.included_tickers
    assert result.subscription_edge == "UNKNOWN"


# --------------------------------------------------------------------
# 3. Evidence semantics: two grades, neither inflated by unused inputs.
# --------------------------------------------------------------------


def test_market_context_never_inflates_either_evidence_grade_or_changes_the_decision():
    from halka_arz_advisor.evds.models import MarketContextFeatureValue, MarketContextSnapshot

    terms = _resolved_terms()
    market = MarketContextSnapshot(
        features={
            "bist100_return_20d": MarketContextFeatureValue(-40.0, date(2026, 8, 8), ("x",)),
            "policy_rate_minus_cpi": MarketContextFeatureValue(-30.0, date(2026, 8, 8), ("x",)),
            "bist_index_level": MarketContextFeatureValue(13000.0, date(2026, 8, 8), ("x",)),
        }
    )

    without_market = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES, market=None))
    with_market = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES, market=market))

    assert without_market.action == with_market.action
    assert without_market.subscription_evidence_grade == with_market.subscription_evidence_grade
    assert without_market.ownership_evidence_grade == with_market.ownership_evidence_grade


def test_subscription_evidence_grade_reflects_regime_maturity_not_financial_ratios():
    terms = _resolved_terms()
    healthy_derived = _derived(
        net_margin=_derived_feature("net_margin", "computed", 20.0),
        current_ratio=_derived_feature("current_ratio", "computed", 3.0),
    )

    strong_regime = tuple(_outcome(f"S{i}", date(2026, 7, 15), 15.0) for i in range(6))  # >= STRONG_EVIDENCE_MATURE_IPO_COUNT
    result = evaluate_subscription_decision(_inputs(terms, derived=healthy_derived, recent_ipo_outcomes=strong_regime))

    assert result.subscription_evidence_grade == "STRONG"
    # Financial ratios must not leak into the subscription-side grade.
    result_no_financials = evaluate_subscription_decision(_inputs(terms, derived=None, recent_ipo_outcomes=strong_regime))
    assert result_no_financials.subscription_evidence_grade == result.subscription_evidence_grade


def test_ownership_evidence_grade_reflects_ratios_and_valuation_not_regime():
    terms = _resolved_terms()
    healthy_derived = _derived(
        net_margin=_derived_feature("net_margin", "computed", 20.0),
        current_ratio=_derived_feature("current_ratio", "computed", 3.0),
    )

    result_favorable_regime = evaluate_subscription_decision(_inputs(terms, derived=healthy_derived, recent_ipo_outcomes=FAVORABLE_OUTCOMES))
    result_no_regime = evaluate_subscription_decision(_inputs(terms, derived=healthy_derived, recent_ipo_outcomes=()))

    assert result_favorable_regime.ownership_evidence_grade == result_no_regime.ownership_evidence_grade
    # No valuation anchor available (no financial_observations passed) -> MODERATE, not STRONG.
    assert result_no_regime.ownership_evidence_grade == "MODERATE"


# --------------------------------------------------------------------
# 4. Ownership semantics: financial_quality vs. ownership_view, gated
#    by a valuation anchor.
# --------------------------------------------------------------------


def test_healthy_ratios_without_a_valuation_anchor_cap_ownership_at_watch():
    terms = _resolved_terms()
    healthy_derived = _derived(
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 15.0),
        net_margin=_derived_feature("net_margin", "computed", 12.0),
        current_ratio=_derived_feature("current_ratio", "computed", 2.0),
    )
    # No financial_observations passed -> ValuationEvidence has no
    # anchor for a P/E, P/S, or P/B multiple.

    result = evaluate_subscription_decision(_inputs(terms, derived=healthy_derived))

    assert result.financial_quality == "POSITIVE"
    assert result.ownership_view == "WATCH"
    assert result.ownership_view != "HOLD_CANDIDATE"
    assert result.valuation_evidence.sufficiency == "INSUFFICIENT"
    assert any("valuation evidence is insufficient" in r for r in result.reasons)


def test_healthy_ratios_with_a_valuation_anchor_reach_hold_candidate():
    terms = _resolved_terms()
    healthy_derived = _derived(
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 15.0),
        net_margin=_derived_feature("net_margin", "computed", 12.0),
        current_ratio=_derived_feature("current_ratio", "computed", 2.0),
    )

    result = evaluate_subscription_decision(
        _inputs(terms, derived=healthy_derived, financial_observations=SUFFICIENT_FINANCIAL_OBSERVATIONS)
    )

    assert result.financial_quality == "POSITIVE"
    assert result.valuation_evidence.sufficiency == "SUFFICIENT"
    assert result.ownership_view == "HOLD_CANDIDATE"


def test_single_red_flag_ratio_forces_negative_quality_and_avoid_long_term():
    terms = _resolved_terms()
    derived = _derived(
        net_margin=_derived_feature("net_margin", "computed", -5.0),
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 25.0),
        current_ratio=_derived_feature("current_ratio", "computed", 3.0),
    )

    result = evaluate_subscription_decision(
        _inputs(terms, derived=derived, recent_ipo_outcomes=FAVORABLE_OUTCOMES, financial_observations=SUFFICIENT_FINANCIAL_OBSERVATIONS)
    )

    assert result.financial_quality == "NEGATIVE"
    assert result.ownership_view == "AVOID_LONG_TERM"
    # A pure short-horizon action still doesn't require good fundamentals.
    assert result.action == "SUBSCRIBE_FOR_LISTING_TRADE"


def test_no_financial_evidence_is_unknown_quality_and_not_assessable():
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms, derived=None))

    assert result.financial_quality == "UNKNOWN"
    assert result.ownership_view == "NOT_ASSESSABLE"


# --------------------------------------------------------------------
# 6. Final action semantics.
# --------------------------------------------------------------------


def test_resolved_terms_and_supportive_mechanics_alone_never_produce_subscribe():
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=()))

    assert result.action not in ("SUBSCRIBE_FOR_LISTING_TRADE", "SUBSCRIBE_WITH_HOLD_OPTION")
    assert result.action == "WATCH_SUBSCRIPTION"


def test_subscribe_with_hold_option_requires_hold_candidate_ownership():
    terms = _resolved_terms()
    healthy_derived = _derived(
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 15.0),
        net_margin=_derived_feature("net_margin", "computed", 12.0),
        current_ratio=_derived_feature("current_ratio", "computed", 2.0),
    )

    result = evaluate_subscription_decision(
        _inputs(terms, derived=healthy_derived, recent_ipo_outcomes=FAVORABLE_OUTCOMES, financial_observations=SUFFICIENT_FINANCIAL_OBSERVATIONS)
    )

    assert result.ownership_view == "HOLD_CANDIDATE"
    assert result.action == "SUBSCRIBE_WITH_HOLD_OPTION"


def test_red_flag_forces_pass_regardless_of_favorable_regime_or_ownership():
    terms = _resolved_terms(new_issue_shares=_field("extracted", 100.0, "shares"))  # breaks the share equation
    healthy_derived = _derived(
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 40.0),
        net_margin=_derived_feature("net_margin", "computed", 20.0),
        current_ratio=_derived_feature("current_ratio", "computed", 3.0),
    )

    result = evaluate_subscription_decision(
        _inputs(terms, derived=healthy_derived, recent_ipo_outcomes=FAVORABLE_OUTCOMES, financial_observations=SUFFICIENT_FINANCIAL_OBSERVATIONS)
    )

    assert result.action == "PASS_SUBSCRIPTION"
    assert result.strongest_risks


def test_withdrawal_disclosure_title_forces_pass_subscription():
    terms = _resolved_terms()
    disclosure = KapDisclosure(
        disclosure_id="d1", disclosure_index=1, published_at=datetime(2026, 8, 1),
        company_name="Örnek A.Ş.", ticker="ORNK", title="Halka Arzın İptali Hakkında",
        summary="", document_type="approved_prospectus", notification_url="https://example/1",
        attachment_urls=(), matched_spk_record_id="ipo:ORNK:2026", match_method="ticker", raw={},
    )

    result = evaluate_subscription_decision(_inputs(terms, disclosures=(disclosure,), recent_ipo_outcomes=FAVORABLE_OUTCOMES))

    assert result.action == "PASS_SUBSCRIPTION"
    assert any("withdrawal" in r for r in result.strongest_risks)


def test_closed_window_with_watchworthy_ownership_reassesses_after_listing():
    terms = _resolved_terms(subscription_end=_field("extracted", date(2026, 8, 1), "date"))
    derived = _derived(net_margin=_derived_feature("net_margin", "computed", 5.0))  # WATCH-tier ownership

    result = evaluate_subscription_decision(_inputs(terms, derived=derived, recent_ipo_outcomes=FAVORABLE_OUTCOMES, as_of=AS_OF))

    assert result.ownership_view == "WATCH"
    assert result.action == "PASS_AND_REASSESS_AFTER_LISTING"
    assert result.intended_horizon == "watch_post_listing"


def test_allocation_scenarios_never_change_the_action():
    """Allocation scenarios are descriptive only — they must never
    independently create or block a subscribe signal."""
    terms = _resolved_terms()

    with_favorable = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=FAVORABLE_OUTCOMES))
    with_no_regime = evaluate_subscription_decision(_inputs(terms, recent_ipo_outcomes=()))

    # Both compute the same scenarios (same offering terms), but only
    # the regime-driven edge determines the action.
    assert with_favorable.allocation_scenarios[0].hypothetical_retail_participant_count == with_no_regime.allocation_scenarios[0].hypothetical_retail_participant_count
    assert with_favorable.action == "SUBSCRIBE_FOR_LISTING_TRADE"
    assert with_no_regime.action == "WATCH_SUBSCRIPTION"


# --------------------------------------------------------------------
# Valuation evidence integration.
# --------------------------------------------------------------------


def test_valuation_evidence_is_exposed_on_the_decision_and_never_recomputed():
    terms = _resolved_terms()

    result = evaluate_subscription_decision(_inputs(terms, financial_observations=SUFFICIENT_FINANCIAL_OBSERVATIONS))

    assert result.valuation_evidence.implied_market_cap.status == "computed"
    assert result.valuation_evidence.pe_at_offer.status == "computed"
    assert result.valuation_evidence.sufficiency == "SUFFICIENT"


def test_healthy_ratios_cannot_substitute_for_valuation_evidence():
    """"Do not let healthy company ratios substitute for valuation" —
    even a POSITIVE financial_quality read must not, on its own, flip
    valuation_evidence.sufficiency or unlock HOLD_CANDIDATE."""
    terms = _resolved_terms()
    healthy_derived = _derived(
        revenue_growth_yoy=_derived_feature("revenue_growth_yoy", "computed", 30.0),
        net_margin=_derived_feature("net_margin", "computed", 25.0),
        current_ratio=_derived_feature("current_ratio", "computed", 4.0),
    )

    result = evaluate_subscription_decision(_inputs(terms, derived=healthy_derived))  # no financial_observations

    assert result.financial_quality == "POSITIVE"
    assert result.valuation_evidence.sufficiency == "INSUFFICIENT"
    assert result.ownership_view == "WATCH"
