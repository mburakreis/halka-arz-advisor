"""Non-destructive shape profiling for raw SPK IPO records.

Reports what's observed in a batch of records — it does not coerce
values, decide a business identity key, or build a domain model. That's
left for a later phase once the shape is well understood.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DuplicateIdentityCandidate:
    """A field where some value repeats across records.

    This is only a candidate — it does not claim the field is (part of)
    the record's real identity key.
    """

    field_name: str
    distinct_non_null_values: int
    duplicated_value_count: int
    example_duplicate_value: object


@dataclass(frozen=True, slots=True)
class RecordShapeProfile:
    record_count: int
    all_observed_keys: frozenset
    keys_missing_from_some_records: frozenset
    keys_always_null: frozenset
    observed_types_per_key: dict
    duplicate_full_records: tuple[tuple[int, ...], ...]
    duplicate_identity_candidates: tuple[DuplicateIdentityCandidate, ...]
    first_record: dict | None
    last_record: dict | None


def profile_records(records: list[dict]) -> RecordShapeProfile:
    record_count = len(records)
    if record_count == 0:
        return RecordShapeProfile(
            record_count=0,
            all_observed_keys=frozenset(),
            keys_missing_from_some_records=frozenset(),
            keys_always_null=frozenset(),
            observed_types_per_key={},
            duplicate_full_records=(),
            duplicate_identity_candidates=(),
            first_record=None,
            last_record=None,
        )

    all_keys: set[str] = set()
    for record in records:
        if isinstance(record, dict):
            all_keys.update(record.keys())

    presence_count = dict.fromkeys(all_keys, 0)
    null_count = dict.fromkeys(all_keys, 0)
    types_per_key: dict[str, set[str]] = {k: set() for k in all_keys}

    for record in records:
        if not isinstance(record, dict):
            continue
        for key in all_keys:
            if key in record:
                presence_count[key] += 1
                value = record[key]
                types_per_key[key].add(type(value).__name__)
                if value is None:
                    null_count[key] += 1

    keys_missing_from_some_records = frozenset(k for k, c in presence_count.items() if c < record_count)
    keys_always_null = frozenset(
        k for k in all_keys if presence_count[k] > 0 and null_count[k] == presence_count[k]
    )

    duplicate_full_records = tuple(
        tuple(idxs) for idxs in _group_duplicate_records(records).values() if len(idxs) > 1
    )

    duplicate_identity_candidates = tuple(_find_duplicate_identity_candidates(records, sorted(all_keys)))

    return RecordShapeProfile(
        record_count=record_count,
        all_observed_keys=frozenset(all_keys),
        keys_missing_from_some_records=keys_missing_from_some_records,
        keys_always_null=keys_always_null,
        observed_types_per_key={k: frozenset(v) for k, v in types_per_key.items()},
        duplicate_full_records=duplicate_full_records,
        duplicate_identity_candidates=duplicate_identity_candidates,
        first_record=records[0] if isinstance(records[0], dict) else None,
        last_record=records[-1] if isinstance(records[-1], dict) else None,
    )


def _group_duplicate_records(records: list[dict]) -> dict[str, list[int]]:
    seen: dict[str, list[int]] = {}
    for index, record in enumerate(records):
        canonical = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
        seen.setdefault(canonical, []).append(index)
    return seen


def _find_duplicate_identity_candidates(
    records: list[dict], keys: list[str]
) -> list[DuplicateIdentityCandidate]:
    candidates = []
    for key in keys:
        counts: dict = {}
        for record in records:
            if not isinstance(record, dict) or key not in record:
                continue
            value = record[key]
            if value is None:
                continue
            hashable_value = value if not isinstance(value, (list, dict)) else json.dumps(
                value, sort_keys=True, default=str
            )
            counts[hashable_value] = counts.get(hashable_value, 0) + 1

        duplicated = {v: c for v, c in counts.items() if c > 1}
        if duplicated:
            candidates.append(
                DuplicateIdentityCandidate(
                    field_name=key,
                    distinct_non_null_values=len(counts),
                    duplicated_value_count=len(duplicated),
                    example_duplicate_value=next(iter(duplicated)),
                )
            )
    return candidates


@dataclass(frozen=True, slots=True)
class OrderingComparison:
    stable: bool
    same_length: bool
    reason: str


def compare_ordering(records_a: list[dict], records_b: list[dict]) -> OrderingComparison:
    """Compare two fetches of (presumably) the same query, position by position."""
    if len(records_a) != len(records_b):
        return OrderingComparison(
            stable=False,
            same_length=False,
            reason=f"record counts differ: {len(records_a)} vs {len(records_b)}",
        )
    for index, (a, b) in enumerate(zip(records_a, records_b)):
        if a != b:
            return OrderingComparison(
                stable=False,
                same_length=True,
                reason=f"records diverge at index {index}",
            )
    return OrderingComparison(
        stable=True,
        same_length=True,
        reason=f"all {len(records_a)} record(s) identical and in the same order across both fetches",
    )
