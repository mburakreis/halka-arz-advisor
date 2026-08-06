"""Stable-enough identity keys for deduplicating SPK records across runs.

These are practical dedup keys for the notification MVP, not a claim
about a record's "real" business identity — that question is explicitly
left open (see the Phase 1A field-shape profiler's
``duplicate_identity_candidates``, which reports candidates without
deciding one). Here we just need a key that's stable run-to-run so a
record already notified about doesn't get re-sent.
"""

from __future__ import annotations

from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord


def ipo_identity(record: SpkIpoRecord) -> str:
    """``borsaKodu`` (stock ticker) is the natural key; it's nullable in
    the schema, so fall back to the company name, combined with ``donem``
    (the reporting period, e.g. "2024 / 2") to reduce collision risk."""
    company_key = record.borsa_kodu or record.sirket_unvani or "unknown"
    return f"ipo:{company_key}:{record.donem or ''}"


def application_identity(record: SpkIpoApplicationRecord) -> str:
    """Company name + application date — both fields are always present
    on a valid :class:`SpkIpoApplicationRecord` (rows failing to produce
    either are rejected by the parser as invalid, not returned here)."""
    return f"application:{record.company_name}:{record.application_date.isoformat()}"
