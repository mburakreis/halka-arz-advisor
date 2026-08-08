"""Human-in-the-loop completion for the small set of pre-offer
``OfferingTerms`` fields a real subscription decision cannot proceed
without, when automatic extraction left them ``not_found``.

This is deliberately *not* a second extraction pipeline and does not
touch :mod:`halka_arz_advisor.kap.extraction`/
:mod:`halka_arz_advisor.kap.offering_terms` at all. A manual value is
stored in its own :class:`ManualFieldConfirmation`, entirely separate
from the automatically extracted :class:`~halka_arz_advisor.kap.offering_terms.OfferingTermField`
it completes — :func:`complete_offering_terms` never mutates or
discards the original extracted evidence, it only pairs the two
together and computes which one is *in force* for downstream
consumers (:class:`CompletedTermField.source`).

Rules (see :func:`complete_offering_terms`):

- Automatic extraction wins whenever it actually resolved a value
  (``status == "extracted"``) — a manual confirmation for an
  already-resolved field is stored (so it stays visible/auditable) but
  never put into effect.
- A ``"conflicting"`` field is **never** silently resolved by a manual
  value — genuine official-source disagreement stays ``"conflicting"``
  regardless of what a human later types in; the manual value is kept
  attached for transparency only, exactly like the already-resolved
  case above.
- Only a genuinely ``"not_found"`` field can actually be completed by
  a manual confirmation.
- A stored confirmation is reusable on every later run: see
  :class:`ManualConfirmationStore`, a one-JSON-file-per-company disk
  cache in the same shape this project already uses for
  :mod:`halka_arz_advisor.kap.backfill_cache`/:mod:`halka_arz_advisor.gemini.cache`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from .offering_terms import OfferingTerms, OfferingTermField, OfferingTermStatus

FieldSource = Literal["automatic", "user_confirmed"]

DEFAULT_MANUAL_CONFIRMATION_CACHE_DIR = Path("data") / "cache" / "manual_offering_terms"

# The exact fields a SubscriptionDecisionV1 gate cannot proceed
# without (see halka_arz_advisor.decision.subscription_v1) and that a
# human can plausibly know/confirm from the same official documents
# even when this project's own regex-based extraction didn't resolve
# them — not "every OfferingTerms field", deliberately kept small.
CONFIRMABLE_OFFERING_TERM_FIELDS: tuple[str, ...] = (
    "offer_price",
    "subscription_start",
    "subscription_end",
    "retail_offered_shares",
    "retail_allocation_percentage",
    "retail_distribution_rule",
    "distribution_method",
    "total_offered_shares",
    # Permits completing the valuation anchor directly (e.g. from a
    # prospectus's own stated post-offer market value) when the
    # automatic offer_price*post_offer_share_count derivation can't
    # resolve — see kap.valuation, the sole reader of this field for a
    # subscription decision.
    "implied_post_money_market_cap",
)

_FIELD_UNITS: dict[str, str | None] = {
    "offer_price": "TRY",
    "subscription_start": "date",
    "subscription_end": "date",
    "retail_offered_shares": "shares",
    "retail_allocation_percentage": "percent",
    "retail_distribution_rule": None,
    "distribution_method": None,
    "total_offered_shares": "shares",
    "implied_post_money_market_cap": "TRY",
}

_DISTRIBUTION_RULE_VALUES = frozenset({"equal", "proportional"})


class ManualConfirmationValidationError(ValueError):
    """A manual confirmation's field/value doesn't fit this project's
    known shape for that field — rejected before it ever reaches
    storage or a decision, rather than silently accepted and
    misinterpreted downstream."""


def _validate_value(field_name: str, value: object) -> None:
    if field_name not in CONFIRMABLE_OFFERING_TERM_FIELDS:
        raise ManualConfirmationValidationError(
            f"{field_name!r} is not a manually confirmable OfferingTerms field "
            f"(allowed: {', '.join(CONFIRMABLE_OFFERING_TERM_FIELDS)})"
        )
    if field_name in ("subscription_start", "subscription_end"):
        if not isinstance(value, date):
            raise ManualConfirmationValidationError(f"{field_name!r} must be a date, got {type(value).__name__}")
        return
    if field_name == "retail_distribution_rule":
        if value not in _DISTRIBUTION_RULE_VALUES:
            raise ManualConfirmationValidationError(
                f"retail_distribution_rule must be one of {sorted(_DISTRIBUTION_RULE_VALUES)}, got {value!r}"
            )
        return
    if field_name == "distribution_method":
        if not isinstance(value, str) or not value.strip():
            raise ManualConfirmationValidationError("distribution_method must be a non-empty string")
        return
    # offer_price / retail_offered_shares / retail_allocation_percentage /
    # total_offered_shares / implied_post_money_market_cap
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManualConfirmationValidationError(f"{field_name!r} must be a number, got {type(value).__name__}")
    if value <= 0:
        raise ManualConfirmationValidationError(f"{field_name!r} must be positive, got {value!r}")


@dataclass(frozen=True, slots=True)
class ManualFieldConfirmation:
    """One human-supplied value for one confirmable ``OfferingTerms``
    field, kept entirely separate from the automatically extracted
    evidence it may complete."""

    field_name: str
    value: object
    confirmed_by: str
    confirmed_at: datetime
    note: str | None = None

    def __post_init__(self) -> None:
        _validate_value(self.field_name, self.value)


@dataclass(frozen=True, slots=True)
class CompletedTermField:
    """The extracted evidence, any manual confirmation on file for the
    same field, and which one is actually *in force*
    (:attr:`source`) — never a merge of the two values."""

    field_name: str
    extracted: OfferingTermField
    manual: ManualFieldConfirmation | None
    effective_status: OfferingTermStatus
    effective_value: object | None
    effective_unit: str | None
    source: FieldSource


@dataclass(frozen=True, slots=True)
class CompletedOfferingTerms:
    offer_price: CompletedTermField
    subscription_start: CompletedTermField
    subscription_end: CompletedTermField
    retail_offered_shares: CompletedTermField
    retail_allocation_percentage: CompletedTermField
    retail_distribution_rule: CompletedTermField
    distribution_method: CompletedTermField
    total_offered_shares: CompletedTermField
    implied_post_money_market_cap: CompletedTermField

    def get(self, field_name: str) -> CompletedTermField:
        return getattr(self, field_name)

    def as_dict(self) -> dict[str, CompletedTermField]:
        return {name: getattr(self, name) for name in CONFIRMABLE_OFFERING_TERM_FIELDS}


def _complete_one_field(field_name: str, extracted: OfferingTermField, manual: ManualFieldConfirmation | None) -> CompletedTermField:
    unit = _FIELD_UNITS[field_name]
    if extracted.status in ("extracted", "conflicting"):
        # Automatic extraction is preferred whenever resolved; a
        # genuine cross-source conflict is never silently resolved by
        # a manual value either — both cases keep the extracted status
        # in force, with the manual confirmation only attached for
        # visibility.
        return CompletedTermField(
            field_name=field_name, extracted=extracted, manual=manual,
            effective_status=extracted.status, effective_value=extracted.value, effective_unit=extracted.unit or unit,
            source="automatic",
        )
    if manual is not None:
        return CompletedTermField(
            field_name=field_name, extracted=extracted, manual=manual,
            effective_status="extracted", effective_value=manual.value, effective_unit=unit,
            source="user_confirmed",
        )
    return CompletedTermField(
        field_name=field_name, extracted=extracted, manual=None,
        effective_status="not_found", effective_value=None, effective_unit=unit,
        source="automatic",
    )


def complete_offering_terms(terms: OfferingTerms, confirmations: Sequence[ManualFieldConfirmation] = ()) -> CompletedOfferingTerms:
    """Pair each confirmable field of ``terms`` with its latest manual
    confirmation (if any) from ``confirmations`` — pure, no I/O; see
    :class:`ManualConfirmationStore` for loading persisted
    confirmations first."""
    by_field = {c.field_name: c for c in confirmations}
    fields = {
        name: _complete_one_field(name, getattr(terms, name), by_field.get(name))
        for name in CONFIRMABLE_OFFERING_TERM_FIELDS
    }
    return CompletedOfferingTerms(**fields)


def effective_offering_terms(terms: OfferingTerms, completed: CompletedOfferingTerms) -> OfferingTerms:
    """A new :class:`~halka_arz_advisor.kap.offering_terms.OfferingTerms`
    with every confirmable field replaced by its *effective* (automatic-
    if-resolved, else user-confirmed-if-available) value — for
    downstream consumers (e.g.
    :func:`halka_arz_advisor.kap.allocation_scenario.build_allocation_scenario`)
    that just want the best currently-known value regardless of source.
    Every other ``OfferingTerms`` field (not in
    :data:`CONFIRMABLE_OFFERING_TERM_FIELDS`) is passed through
    unchanged. The original ``terms``/``completed`` remain the source
    of truth for provenance and for telling automatic and manual values
    apart — this is a convenience view, not a replacement for either.
    """
    overrides = {}
    for name in CONFIRMABLE_OFFERING_TERM_FIELDS:
        completed_field = completed.get(name)
        overrides[name] = OfferingTermField(
            status=completed_field.effective_status,
            value=completed_field.effective_value,
            unit=completed_field.effective_unit,
            derived=False,
            observations=completed_field.extracted.observations,
            notes="user_confirmed" if completed_field.source == "user_confirmed" else completed_field.extracted.notes,
        )
    return replace(terms, **overrides)


class ManualConfirmationStore:
    """Disk cache of manual confirmations, one JSON file per SPK
    ``record_id`` — mirrors :class:`~halka_arz_advisor.kap.backfill_cache.BackfillCache`'s
    shape. A later confirmation for the same field overwrites the
    earlier one on disk (the latest human input wins over an older one
    for the *same* field — this is separate from, and does not affect,
    the "extracted beats manual" rule in :func:`complete_offering_terms`,
    which only compares a manual value against automatic extraction,
    never manual-against-manual)."""

    def __init__(self, directory: Path = DEFAULT_MANUAL_CONFIRMATION_CACHE_DIR) -> None:
        self.directory = Path(directory)

    def _path(self, record_id: str) -> Path:
        safe_name = record_id.replace("/", "_").replace(" ", "")
        return self.directory / f"{safe_name}.json"

    def get(self, record_id: str) -> tuple[ManualFieldConfirmation, ...]:
        path = self._path(record_id)
        if not path.exists():
            return ()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return tuple(_confirmation_from_dict(entry) for entry in raw)

    def add_confirmation(self, record_id: str, confirmation: ManualFieldConfirmation) -> None:
        """Upsert ``confirmation`` by ``field_name`` into ``record_id``'s
        stored set and persist immediately."""
        existing = {c.field_name: c for c in self.get(record_id)}
        existing[confirmation.field_name] = confirmation
        self.directory.mkdir(parents=True, exist_ok=True)
        ordered = sorted(existing.values(), key=lambda c: c.field_name)
        self._path(record_id).write_text(
            json.dumps([_confirmation_as_dict(c) for c in ordered], indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _confirmation_as_dict(confirmation: ManualFieldConfirmation) -> dict:
    value = confirmation.value
    value_json = value.isoformat() if isinstance(value, date) else value
    return {
        "field_name": confirmation.field_name,
        "value": value_json,
        "confirmed_by": confirmation.confirmed_by,
        "confirmed_at": confirmation.confirmed_at.isoformat(),
        "note": confirmation.note,
    }


def _confirmation_from_dict(data: dict) -> ManualFieldConfirmation:
    field_name = data["field_name"]
    raw_value = data["value"]
    value: object = date.fromisoformat(raw_value) if field_name in ("subscription_start", "subscription_end") else raw_value
    return ManualFieldConfirmation(
        field_name=field_name,
        value=value,
        confirmed_by=data["confirmed_by"],
        confirmed_at=datetime.fromisoformat(data["confirmed_at"]),
        note=data.get("note"),
    )


def completed_term_field_as_dict(field: CompletedTermField) -> dict:
    manual = field.manual
    return {
        "field_name": field.field_name,
        "source": field.source,
        "effective_status": field.effective_status,
        "effective_value": field.effective_value.isoformat() if isinstance(field.effective_value, date) else field.effective_value,
        "effective_unit": field.effective_unit,
        "extracted_status": field.extracted.status,
        "manual_confirmation": (
            {
                "value": manual.value.isoformat() if isinstance(manual.value, date) else manual.value,
                "confirmed_by": manual.confirmed_by,
                "confirmed_at": manual.confirmed_at.isoformat(),
                "note": manual.note,
                "in_effect": field.source == "user_confirmed",
            }
            if manual is not None
            else None
        ),
    }


def completed_offering_terms_as_dict(completed: CompletedOfferingTerms) -> dict:
    return {name: completed_term_field_as_dict(getattr(completed, name)) for name in CONFIRMABLE_OFFERING_TERM_FIELDS}


__all__ = [
    "CONFIRMABLE_OFFERING_TERM_FIELDS",
    "DEFAULT_MANUAL_CONFIRMATION_CACHE_DIR",
    "CompletedOfferingTerms",
    "CompletedTermField",
    "FieldSource",
    "ManualConfirmationStore",
    "ManualConfirmationValidationError",
    "ManualFieldConfirmation",
    "complete_offering_terms",
    "completed_offering_terms_as_dict",
    "completed_term_field_as_dict",
    "effective_offering_terms",
]
