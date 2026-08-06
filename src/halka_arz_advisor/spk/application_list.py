"""Production parser and client for SPK's IPO application list page.

https://spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu

Phase 0/1A's diagnostic (``scripts/inspect_spk_application_table.py``)
found exactly one ``<table>`` there: a 3-column ``[sıra, şirket, tarih]``
layout whose header row is rendered with plain ``<td>`` cells (no
``<thead>``, no ``<th>``) — so it looks structurally identical to a data
row and has to be told apart by content, not by tag name.

This module turns that into typed records. It does not match these
applications against completed IPOs (a separate, later phase) and does
not decide anything about investment merit — it only extracts, validates,
and normalizes what's on the page.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .exceptions import SpkApplicationTableError, SpkResponseError, SpkTransportError

logger = logging.getLogger(__name__)

APPLICATION_LIST_URL = "https://spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu"

_DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
_HEADER_KEYWORDS_COMPANY = ("şirket", "sirket")
_HEADER_KEYWORDS_DATE = ("tarih",)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpkIpoApplicationRecord:
    """One row of the IPO application list, normalized but not reinterpreted.

    ``company_name`` is the original text as published (only
    surrounding whitespace is trimmed — no case changes, no stripping of
    legal-form suffixes like "A.Ş."). ``raw_row`` preserves every cell
    exactly as scraped, for traceability independent of this module's
    parsing choices.
    """

    company_name: str
    application_date: date
    application_date_raw: str
    raw_row: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InvalidApplicationRow:
    """A table row that could not be turned into a record, and why."""

    row_index: int
    raw_row: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ApplicationTableParseResult:
    records: tuple[SpkIpoApplicationRecord, ...]
    invalid_rows: tuple[InvalidApplicationRow, ...]
    table_count: int
    used_table_index: int


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _row_cells(tr) -> list[str]:
    return [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]


def _looks_like_header_row(cells: list[str]) -> bool:
    """True for the one ``<td>``-only header row, not a real data row.

    A real data row's last cell is always a ``DD.MM.YYYY`` date. The
    header row's cells instead spell out the column labels (observed
    live as ``["", "Şirketler", "BaşvuruTarihi"]``) — so "doesn't look
    like a date, and mentions the expected header words" is enough to
    tell them apart without depending on ``<th>``.
    """
    if not cells:
        return False
    joined = " ".join(cells).lower()
    has_company_word = any(kw in joined for kw in _HEADER_KEYWORDS_COMPANY)
    has_date_word = any(kw in joined for kw in _HEADER_KEYWORDS_DATE)
    last_cell_is_date = bool(_DATE_RE.match(cells[-1].strip()))
    return has_company_word and has_date_word and not last_cell_is_date


def _looks_like_ipo_application_table(table) -> bool:
    rows = table.find_all("tr")
    if not rows:
        return False
    sample_cells = [_row_cells(r) for r in rows[:5]]
    haystack = " ".join(" ".join(cells) for cells in sample_cells).lower()
    return ("şirket" in haystack or "sirket" in haystack) and "tarih" in haystack


def _select_application_table(tables: list) -> int:
    matches = [i for i, t in enumerate(tables) if _looks_like_ipo_application_table(t)]
    if not matches:
        raise SpkApplicationTableError(
            f"found {len(tables)} <table> element(s) but none look like the IPO "
            "application table (expected company/date header keywords)"
        )
    if len(matches) > 1:
        raise SpkApplicationTableError(
            f"multiple tables ({matches}) look like the IPO application table; "
            "refusing to guess which one to use"
        )
    return matches[0]


def _parse_row(cells: list[str], *, row_index: int) -> SpkIpoApplicationRecord | InvalidApplicationRow:
    raw_row = tuple(cells)

    if len(cells) < 2:
        return InvalidApplicationRow(row_index, raw_row, f"expected at least 2 cells, got {len(cells)}")

    # Observed layout is [sıra, şirket, tarih]; tolerate a 2-column row
    # (no sequence number) by taking the last two cells as company/date.
    company_raw, date_raw = cells[-2], cells[-1]

    company_name = company_raw.strip()
    if not company_name:
        return InvalidApplicationRow(row_index, raw_row, "empty company name")

    date_raw_stripped = date_raw.strip()
    if not _DATE_RE.match(date_raw_stripped):
        return InvalidApplicationRow(
            row_index, raw_row, f"application date {date_raw_stripped!r} does not match DD.MM.YYYY"
        )
    try:
        parsed_date = datetime.strptime(date_raw_stripped, "%d.%m.%Y").date()
    except ValueError as exc:
        return InvalidApplicationRow(
            row_index, raw_row, f"{date_raw_stripped!r} is not a valid calendar date: {exc}"
        )

    return SpkIpoApplicationRecord(
        company_name=company_name,
        application_date=parsed_date,
        application_date_raw=date_raw_stripped,
        raw_row=raw_row,
    )


def parse_application_table(html: str) -> ApplicationTableParseResult:
    """Parse the IPO application table out of the page's raw HTML.

    Raises :class:`SpkApplicationTableError` if no single table can be
    confidently identified as the application list. Per-row problems
    (bad date, empty company name) are never raised — they're collected
    into ``invalid_rows`` and logged as warnings, so one bad row doesn't
    lose every other row on the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise SpkApplicationTableError("no <table> found on the SPK IPO application page")

    table_index = _select_application_table(tables)
    table = tables[table_index]

    records: list[SpkIpoApplicationRecord] = []
    invalid_rows: list[InvalidApplicationRow] = []
    header_seen = False
    data_row_position = 0

    for tr in table.find_all("tr"):
        cells = _row_cells(tr)
        if not cells or all(c == "" for c in cells):
            continue  # a fully blank spacer row: not data, not an error

        if not header_seen and _looks_like_header_row(cells):
            header_seen = True
            continue
        header_seen = True  # only the first substantive row can be the header

        data_row_position += 1
        parsed = _parse_row(cells, row_index=data_row_position)
        if isinstance(parsed, SpkIpoApplicationRecord):
            records.append(parsed)
        else:
            invalid_rows.append(parsed)
            logger.warning("rejected SPK IPO application row %d: %s (%r)", parsed.row_index, parsed.reason, parsed.raw_row)

    return ApplicationTableParseResult(
        records=tuple(records),
        invalid_rows=tuple(invalid_rows),
        table_count=len(tables),
        used_table_index=table_index,
    )


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


@dataclass(slots=True)
class SpkApplicationPageRawResponse:
    """The raw, unparsed page fetch — preserved before any HTML parsing."""

    requested_url: str
    final_url: str
    http_status: int
    content_type: str | None
    elapsed_ms: float
    html: str


class SpkApplicationListClient:
    """Client for the SPK IPO application list HTML page.

    Uses the same shared HTTP conventions (timeouts, retries, User-Agent)
    as the rest of the project via
    :mod:`halka_arz_advisor.probe.http_client`.
    """

    def __init__(self, config: ProbeConfig | None = None, *, client: httpx.Client | None = None) -> None:
        self._config = config or ProbeConfig()
        self._owns_client = client is None
        self._client = client or build_client(self._config)

    def __enter__(self) -> "SpkApplicationListClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_raw(self) -> SpkApplicationPageRawResponse:
        start = time.monotonic()
        try:
            response = fetch_with_retry(
                self._client, APPLICATION_LIST_URL, self._config, headers={"Accept": "text/html"}
            )
        except httpx.TransportError as exc:
            raise SpkTransportError(
                f"transport failure fetching SPK IPO application page from {APPLICATION_LIST_URL}: {exc}"
            ) from exc
        elapsed_ms = (time.monotonic() - start) * 1000

        if response.status_code >= 400:
            raise SpkResponseError(f"SPK IPO application page returned HTTP {response.status_code}")

        content_type = response.headers.get("content-type")
        if not content_type or "html" not in content_type.lower():
            raise SpkResponseError(
                f"SPK IPO application page returned non-HTML content-type {content_type!r}"
            )

        return SpkApplicationPageRawResponse(
            requested_url=APPLICATION_LIST_URL,
            final_url=str(response.url),
            http_status=response.status_code,
            content_type=content_type,
            elapsed_ms=elapsed_ms,
            html=response.text,
        )

    def fetch_applications(self) -> ApplicationTableParseResult:
        """Fetch and parse the page: the full result, including invalid rows."""
        raw = self.fetch_raw()
        return parse_application_table(raw.html)

    def get_applications(self) -> list[SpkIpoApplicationRecord]:
        """Fetch and parse the page, returning only the valid typed records.

        Use :meth:`fetch_applications` instead if you also need
        ``invalid_rows`` (e.g. to report or log them).
        """
        return list(self.fetch_applications().records)
