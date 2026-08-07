"""Disk cache of what :mod:`halka_arz_advisor.kap.backfill`'s historical
KAP disclosure-list search has already found (or exhaustively searched
for and not found) for one matched company.

Mirrors :mod:`halka_arz_advisor.gemini.cache`'s shape (one JSON file per
key, explicit to/from-dict serialization) for the same reason: the KAP
disclosure-list endpoint has no per-company filter (see
:mod:`halka_arz_advisor.kap.client`), so a historical search means
fetching a bounded date range and filtering client-side — real network
and CPU cost that a scheduled run should never repeat once a company's
lifecycle window has already been searched. What's persisted here is
deliberately just enough of each found disclosure's *pre-processing*
shape (see :meth:`BackfillDisclosureSeed.to_disclosure`) to feed it back
through the existing :func:`~halka_arz_advisor.kap.documents.process_disclosure_documents`
pipeline on a later run — cheap (the PDF itself is already in
:class:`~halka_arz_advisor.kap.pdf.PdfCache`, so this never re-downloads
anything) — rather than a already-processed snapshot, which would go
stale the moment extraction logic changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .classification import DocumentType
from .models import KapDisclosure


@dataclass(frozen=True, slots=True)
class BackfillDisclosureSeed:
    """The subset of a matched :class:`~halka_arz_advisor.kap.models.KapDisclosure`
    fields that exist *before* :func:`~halka_arz_advisor.kap.documents.process_disclosure_documents`
    runs — everything :func:`halka_arz_advisor.kap.matching.match_disclosure`
    itself doesn't add is discovered fresh from ``disclosure_index`` each
    time this is reprocessed (attachment metadata is always resolved
    live regardless of cache_only — see that function's docstring), so
    there's nothing stale to carry here beyond identity/classification."""

    disclosure_id: str
    disclosure_index: int | None
    published_at: datetime
    company_name: str
    ticker: str | None
    title: str
    summary: str
    document_type: DocumentType
    notification_url: str
    matched_spk_record_id: str
    match_method: str

    def to_disclosure(self) -> KapDisclosure:
        return KapDisclosure(
            disclosure_id=self.disclosure_id,
            disclosure_index=self.disclosure_index,
            published_at=self.published_at,
            company_name=self.company_name,
            ticker=self.ticker,
            title=self.title,
            summary=self.summary,
            document_type=self.document_type,
            notification_url=self.notification_url,
            attachment_urls=(),
            matched_spk_record_id=self.matched_spk_record_id,
            match_method=self.match_method,
            raw={},
        )

    @staticmethod
    def from_disclosure(disclosure: KapDisclosure) -> "BackfillDisclosureSeed":
        if disclosure.matched_spk_record_id is None:
            raise ValueError("only a matched disclosure can become a backfill seed")
        return BackfillDisclosureSeed(
            disclosure_id=disclosure.disclosure_id,
            disclosure_index=disclosure.disclosure_index,
            published_at=disclosure.published_at,
            company_name=disclosure.company_name,
            ticker=disclosure.ticker,
            title=disclosure.title,
            summary=disclosure.summary,
            document_type=disclosure.document_type,
            notification_url=disclosure.notification_url,
            matched_spk_record_id=disclosure.matched_spk_record_id,
            match_method=disclosure.match_method,
        )


@dataclass(frozen=True, slots=True)
class BackfillEntry:
    """What's known so far about one matched company's historical
    document backfill. ``searched_from``/``searched_to`` is the union of
    every window a search has actually covered — a later search whose
    window falls entirely inside this range is redundant and skipped
    (see :func:`halka_arz_advisor.kap.backfill.search_and_backfill`).
    ``exhausted_document_types`` are types searched for within that
    window and genuinely not found — not re-attempted until the window
    itself grows (there is no reason to expect a re-search of the exact
    same range to find something new)."""

    record_id: str
    searched_from: date
    searched_to: date
    exhausted_document_types: tuple[str, ...]
    seeds: tuple[BackfillDisclosureSeed, ...]
    updated_at: datetime


def _seed_to_dict(seed: BackfillDisclosureSeed) -> dict:
    return {
        "disclosure_id": seed.disclosure_id,
        "disclosure_index": seed.disclosure_index,
        "published_at": seed.published_at.isoformat(),
        "company_name": seed.company_name,
        "ticker": seed.ticker,
        "title": seed.title,
        "summary": seed.summary,
        "document_type": seed.document_type,
        "notification_url": seed.notification_url,
        "matched_spk_record_id": seed.matched_spk_record_id,
        "match_method": seed.match_method,
    }


def _seed_from_dict(data: dict) -> BackfillDisclosureSeed:
    return BackfillDisclosureSeed(
        disclosure_id=data["disclosure_id"],
        disclosure_index=data.get("disclosure_index"),
        published_at=datetime.fromisoformat(data["published_at"]),
        company_name=data["company_name"],
        ticker=data.get("ticker"),
        title=data["title"],
        summary=data.get("summary", ""),
        document_type=data["document_type"],
        notification_url=data.get("notification_url", ""),
        matched_spk_record_id=data["matched_spk_record_id"],
        match_method=data["match_method"],
    )


def _entry_to_dict(entry: BackfillEntry) -> dict:
    return {
        "record_id": entry.record_id,
        "searched_from": entry.searched_from.isoformat(),
        "searched_to": entry.searched_to.isoformat(),
        "exhausted_document_types": list(entry.exhausted_document_types),
        "seeds": [_seed_to_dict(s) for s in entry.seeds],
        "updated_at": entry.updated_at.isoformat(),
    }


def _entry_from_dict(data: dict) -> BackfillEntry:
    return BackfillEntry(
        record_id=data["record_id"],
        searched_from=date.fromisoformat(data["searched_from"]),
        searched_to=date.fromisoformat(data["searched_to"]),
        exhausted_document_types=tuple(data.get("exhausted_document_types", [])),
        seeds=tuple(_seed_from_dict(s) for s in data.get("seeds", [])),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


class BackfillCache:
    """Disk cache for :class:`BackfillEntry`, one JSON file per matched
    company (keyed by a hash of ``record_id``, which itself contains
    characters — ``/``, spaces — that aren't safe filenames)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, record_id: str) -> Path:
        key = hashlib.sha256(record_id.encode("utf-8")).hexdigest()
        return self.directory / f"{key}.json"

    def get(self, record_id: str) -> BackfillEntry | None:
        path = self._path(record_id)
        if not path.exists():
            return None
        return _entry_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def put(self, record_id: str, entry: BackfillEntry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(record_id).write_text(json.dumps(_entry_to_dict(entry), indent=2, ensure_ascii=False), encoding="utf-8")
