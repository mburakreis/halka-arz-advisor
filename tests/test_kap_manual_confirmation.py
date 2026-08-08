from datetime import date, datetime

import pytest

from halka_arz_advisor.kap.manual_confirmation import (
    CONFIRMABLE_OFFERING_TERM_FIELDS,
    ManualConfirmationStore,
    ManualConfirmationValidationError,
    ManualFieldConfirmation,
    complete_offering_terms,
    effective_offering_terms,
)
from halka_arz_advisor.kap.offering_terms import OFFERING_TERM_FIELD_NAMES, OfferingTerms, OfferingTermField


def _field(status="not_found", value=None, unit=None, observations=()):
    return OfferingTermField(status=status, value=value, unit=unit, derived=False, observations=observations)


def _terms(**overrides) -> OfferingTerms:
    base = {name: _field() for name in OFFERING_TERM_FIELD_NAMES}
    base.update(overrides)
    return OfferingTerms(**base)


def test_manual_confirmation_completes_a_not_found_field_without_touching_extracted_evidence():
    terms = _terms(offer_price=_field("not_found"))
    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8))

    completed = complete_offering_terms(terms, [confirmation])

    assert completed.offer_price.source == "user_confirmed"
    assert completed.offer_price.effective_status == "extracted"
    assert completed.offer_price.effective_value == 12.5
    # The original extracted evidence is untouched, not overwritten.
    assert completed.offer_price.extracted.status == "not_found"
    assert completed.offer_price.manual is confirmation


def test_automatic_extraction_is_preferred_when_already_resolved():
    extracted = _field("extracted", 45.0, "TRY", observations=())
    terms = _terms(offer_price=extracted)
    confirmation = ManualFieldConfirmation("offer_price", 999.0, "burak", datetime(2026, 8, 8))

    completed = complete_offering_terms(terms, [confirmation])

    assert completed.offer_price.source == "automatic"
    assert completed.offer_price.effective_value == 45.0
    # The manual value is still stored/visible, just not in effect.
    assert completed.offer_price.manual == confirmation


def test_official_conflict_is_never_silently_resolved_by_manual_input():
    conflicting = _field("conflicting", None, "TRY")
    terms = _terms(offer_price=conflicting)
    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8))

    completed = complete_offering_terms(terms, [confirmation])

    assert completed.offer_price.effective_status == "conflicting"
    assert completed.offer_price.effective_value is None
    assert completed.offer_price.source == "automatic"
    # The confirmation is visible for transparency, but never applied.
    assert completed.offer_price.manual == confirmation


def test_effective_offering_terms_reflects_manual_values_for_downstream_consumers():
    terms = _terms(
        retail_distribution_rule=_field("not_found"),
        retail_offered_shares=_field("not_found"),
        offer_price=_field("extracted", 45.0, "TRY"),
    )
    confirmations = [
        ManualFieldConfirmation("retail_distribution_rule", "equal", "burak", datetime(2026, 8, 8)),
        ManualFieldConfirmation("retail_offered_shares", 400_000.0, "burak", datetime(2026, 8, 8)),
    ]
    completed = complete_offering_terms(terms, confirmations)
    effective = effective_offering_terms(terms, completed)

    assert effective.retail_distribution_rule.value == "equal"
    assert effective.retail_offered_shares.value == 400_000.0
    # offer_price wasn't confirmable-missing, so it passes through unchanged.
    assert effective.offer_price.value == 45.0
    # A field outside the confirmable set is passed through unchanged too.
    assert effective.gross_offer_size.status == "not_found"


def test_manual_confirmations_are_reusable_across_runs_via_the_store(tmp_path):
    store = ManualConfirmationStore(tmp_path)
    confirmation = ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8), note="from the announcement PDF")

    store.add_confirmation("ipo:ORNK:2026", confirmation)
    reloaded = ManualConfirmationStore(tmp_path).get("ipo:ORNK:2026")

    assert reloaded == (confirmation,)


def test_store_upserts_by_field_name():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        store = ManualConfirmationStore(Path(tmp))
        store.add_confirmation("ipo:ORNK:2026", ManualFieldConfirmation("offer_price", 12.5, "burak", datetime(2026, 8, 8)))
        store.add_confirmation("ipo:ORNK:2026", ManualFieldConfirmation("offer_price", 13.0, "burak", datetime(2026, 8, 9)))
        store.add_confirmation("ipo:ORNK:2026", ManualFieldConfirmation("distribution_method", "sabit fiyatla talep toplama", "burak", datetime(2026, 8, 8)))

        confirmations = store.get("ipo:ORNK:2026")
        assert len(confirmations) == 2
        offer_price_confirmation = next(c for c in confirmations if c.field_name == "offer_price")
        assert offer_price_confirmation.value == 13.0


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("offer_price", -5.0),
        ("offer_price", "not a number"),
        ("subscription_start", "2026-08-01"),
        ("retail_distribution_rule", "randomized"),
        ("distribution_method", ""),
        ("not_a_real_field", 1.0),
    ],
)
def test_invalid_manual_values_are_rejected(field_name, bad_value):
    with pytest.raises(ManualConfirmationValidationError):
        ManualFieldConfirmation(field_name, bad_value, "burak", datetime(2026, 8, 8))


def test_confirmable_fields_matches_the_documented_critical_set():
    assert set(CONFIRMABLE_OFFERING_TERM_FIELDS) == {
        "offer_price",
        "subscription_start",
        "subscription_end",
        "retail_offered_shares",
        "retail_allocation_percentage",
        "retail_distribution_rule",
        "distribution_method",
        "total_offered_shares",
        "implied_post_money_market_cap",
    }


def test_subscription_start_accepts_a_real_date():
    confirmation = ManualFieldConfirmation("subscription_start", date(2026, 8, 1), "burak", datetime(2026, 8, 8))
    assert confirmation.value == date(2026, 8, 1)
