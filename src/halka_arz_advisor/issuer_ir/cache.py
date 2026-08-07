"""Disk cache of one issuer's investor-relations page crawl — what was
discovered and what of that was actually ingested (downloaded, cached,
extracted) — so a scheduled run never repeats an already-satisfied
crawl of the same page.

One JSON file per ticker (tickers are already filesystem-safe, unlike
an SPK record identity — see :mod:`halka_arz_advisor.kap.backfill_cache`
for why that one hashes its key instead).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..kap.classification import DocumentType


@dataclass(frozen=True, slots=True)
class IngestedIssuerDocument:
    """One issuer_ir PDF that was actually downloaded and cached —
    enough to reconstruct a :class:`~halka_arz_advisor.kap.models.KapDisclosure`
    for re-extraction on a later run without crawling or re-downloading
    anything (the PDF itself is already in
    :class:`~halka_arz_advisor.kap.pdf.PdfCache`, keyed by ``obj_id``)."""

    url: str
    link_text: str
    document_type: DocumentType
    obj_id: str
    content_hash: str
    matched_spk_record_id: str


@dataclass(frozen=True, slots=True)
class IssuerIrCacheEntry:
    ticker: str
    crawled_at: datetime
    # Every same-domain, classified PDF link seen on the page, whether
    # or not it was ingested (e.g. it duplicated an already-known
    # content hash, or its type wasn't what was being searched for that
    # run) — kept for diagnostics/reporting.
    discovered_link_count: int
    ingested: tuple[IngestedIssuerDocument, ...]


def _document_to_dict(doc: IngestedIssuerDocument) -> dict:
    return {
        "url": doc.url,
        "link_text": doc.link_text,
        "document_type": doc.document_type,
        "obj_id": doc.obj_id,
        "content_hash": doc.content_hash,
        "matched_spk_record_id": doc.matched_spk_record_id,
    }


def _document_from_dict(data: dict) -> IngestedIssuerDocument:
    return IngestedIssuerDocument(
        url=data["url"],
        link_text=data.get("link_text", ""),
        document_type=data["document_type"],
        obj_id=data["obj_id"],
        content_hash=data["content_hash"],
        matched_spk_record_id=data["matched_spk_record_id"],
    )


def _entry_to_dict(entry: IssuerIrCacheEntry) -> dict:
    return {
        "ticker": entry.ticker,
        "crawled_at": entry.crawled_at.isoformat(),
        "discovered_link_count": entry.discovered_link_count,
        "ingested": [_document_to_dict(d) for d in entry.ingested],
    }


def _entry_from_dict(data: dict) -> IssuerIrCacheEntry:
    return IssuerIrCacheEntry(
        ticker=data["ticker"],
        crawled_at=datetime.fromisoformat(data["crawled_at"]),
        discovered_link_count=data.get("discovered_link_count", 0),
        ingested=tuple(_document_from_dict(d) for d in data.get("ingested", [])),
    )


class IssuerIrCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, ticker: str) -> Path:
        return self.directory / f"{ticker.strip().upper()}.json"

    def get(self, ticker: str) -> IssuerIrCacheEntry | None:
        path = self._path(ticker)
        if not path.exists():
            return None
        return _entry_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def put(self, ticker: str, entry: IssuerIrCacheEntry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(ticker).write_text(json.dumps(_entry_to_dict(entry), indent=2, ensure_ascii=False), encoding="utf-8")
