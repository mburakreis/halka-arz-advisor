"""Historical KAP document backfill for a matched company whose current
(recent-window) disclosures leave one or more of the five supported IPO
document types (see :func:`~halka_arz_advisor.kap.classification.target_document_types`)
unread — e.g. a completed IPO whose approved prospectus was filed
further back than the normal fetch pipeline's lookback window.

Reuses the existing pipeline end to end and adds nothing new to it: a
historical disclosure found here is matched via
:func:`halka_arz_advisor.kap.matching.match_disclosure` (identical
ambiguity safeguards — an ambiguous historical disclosure is left
unmatched, never guessed at) and processed via
:func:`halka_arz_advisor.kap.documents.process_disclosure_documents`
(same :class:`~halka_arz_advisor.kap.pdf.PdfCache`, same OCR fallback,
same field extraction). No new extractor, document type, scoring input,
Gemini behavior, or Telegram formatting is added anywhere in this
module.

The KAP disclosure-list endpoint has no per-company filter (see
:mod:`halka_arz_advisor.kap.client`'s module docstring) — a historical
search means fetching a bounded date range and filtering client-side,
exactly like the normal recent-window fetch already does. That range is
bounded by :func:`lifecycle_window`, derived from the company's own SPK
application/completed-IPO dates rather than scanning unlimited history.
What a search already found — or exhaustively searched for within a
given window and didn't find — is persisted via
:mod:`halka_arz_advisor.kap.backfill_cache` so a scheduled run never
repeats an already-exhausted historical search for the same company.

Two entry points, deliberately separated by cost:

- :func:`search_and_backfill` — the expensive path (a real historical
  KAP disclosure-list fetch, only when still needed). Called from
  ``scripts/backfill_kap_history.py`` only.
- :func:`reprocess_backfilled_disclosures` /
  :func:`merge_backfilled_disclosures` — cheap, network-search-free:
  just re-attaches whatever a prior backfill run already found (the PDF
  is already cached; only a small live attachment-metadata call is
  made, identical to how every other disclosure is already processed).
  Safe to call on every run of ``scripts/analyze_pending_ipos.py``,
  ``scripts/send_pending_analyses.py``, and
  ``scripts/validate_decision_engine.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from ..notify.identity import application_identity, ipo_identity
from ..probe.config import ProbeConfig
from ..spk.application_list import SpkIpoApplicationRecord
from ..spk.models import SpkIpoRecord
from .backfill_cache import BackfillCache, BackfillDisclosureSeed, BackfillEntry
from .classification import DocumentType, TARGET_DOCUMENT_TYPES, classify_prospectus_document_role
from .client import KapClient
from .documents import process_disclosure_documents
from .matching import match_disclosure
from .models import KapDisclosure
from .ocr import OcrCache, OcrConfig
from .pdf import PdfCache

# A readable document found this far before an application was filed is
# still plausibly part of the same offering's paper trail (a prospectus
# is sometimes filed before the application shows up in SPK's own
# list); a first, explicit, revisable judgment call, not a fitted value.
LOOKBACK_BEFORE_APPLICATION_DAYS = 60

# Used only when there's no application_record to anchor to — SpkIpoRecord
# itself has no explicit start-of-process date field, only `donem`
# ("YYYY / M", the reporting period) and, once trading has started,
# `borsada_islem_gorme_tarihi`. A wider margin than the application-date
# one above since `donem` is a much rougher anchor.
LOOKBACK_BEFORE_PERIOD_DAYS = 180

# A document (e.g. "İşlem Görmeye Başlama") can be filed shortly after
# the SPK record's own listing date.
LOOKAHEAD_AFTER_LISTING_DAYS = 30

# Hard cap on the total window width, regardless of how far apart the
# above anchors are — "a reasonable IPO lifecycle window", never
# unbounded history.
MAX_WINDOW_DAYS = 400


@dataclass(frozen=True, slots=True)
class BackfillOutcome:
    """What one :func:`search_and_backfill` call did for one company."""

    record_id: str
    # Every KapDisclosure now available for this company from backfill —
    # both freshly found this call and reprocessed from a prior one;
    # merge into the caller's own disclosure list before computing
    # decision results.
    disclosures: tuple[KapDisclosure, ...]
    # Document types a *fresh* search this call actually recovered
    # (empty if no search ran, or the search found nothing new).
    recovered_document_types: tuple[str, ...]
    # False when no network search ran at all this call — either
    # nothing was missing, or the relevant window was already
    # exhaustively searched by an earlier run.
    searched: bool
    window: tuple[date, date] | None


def _is_readable(disclosure: KapDisclosure) -> bool:
    return disclosure.pdf_status == "ok" or disclosure.ocr_status in ("ocr_ok", "ocr_partial")


def _satisfies_document_type(disclosure: KapDisclosure) -> bool:
    """Whether a readable ``disclosure`` actually counts as *having*
    its ``document_type`` — plain readability for every type except
    ``approved_prospectus``, where a whole bundle of exhibits (audit/
    valuation/legal/charter/fund-use reports, ...) shares that same KAP
    classification alongside the real prospectus body (see
    :func:`~halka_arz_advisor.kap.classification.classify_prospectus_document_role`'s
    docstring) — only a ``"base_document"``-role one satisfies it."""
    if not _is_readable(disclosure):
        return False
    if disclosure.document_type != "approved_prospectus":
        return True
    return classify_prospectus_document_role(disclosure.summary, disclosure.title) == "base_document"


def missing_document_types(disclosures_for_company: Sequence[KapDisclosure]) -> tuple[DocumentType, ...]:
    """Which of the five supported document types this company has no
    *readable* disclosure for yet — a digital-text-layer PDF
    (``pdf_status == "ok"``) or a usable OCR fallback
    (``ocr_status in ("ocr_ok", "ocr_partial")``); an attachment that
    merely exists but never parsed doesn't count. For
    ``approved_prospectus`` specifically, a readable *exhibit* (as
    opposed to the base document itself or one of its parts/revisions)
    doesn't count either — see :func:`_satisfies_document_type`."""
    found = {d.document_type for d in disclosures_for_company if d.document_type in TARGET_DOCUMENT_TYPES and _satisfies_document_type(d)}
    return tuple(t for t in TARGET_DOCUMENT_TYPES if t not in found)


def _period_start_date(donem: str | None) -> date | None:
    """Parse ``SpkIpoRecord.donem`` (``"YYYY / M"``) into the first day
    of that month — a rough anchor only, never used for scoring."""
    if not donem:
        return None
    parts = donem.split("/")
    if len(parts) != 2:
        return None
    try:
        year = int(parts[0].strip())
        month = int(parts[1].strip())
        return date(year, month, 1)
    except ValueError:
        return None


def lifecycle_window(
    *,
    ipo_record: SpkIpoRecord | None,
    application_record: SpkIpoApplicationRecord | None,
    reference_date: date,
) -> tuple[date, date]:
    """The bounded ``[from, to]`` date range a historical search is
    allowed to cover for one company — anchored on whatever SPK dates
    are actually available, always capped at :data:`MAX_WINDOW_DAYS`
    wide and never past ``reference_date``."""
    lower_candidates: list[date] = []
    upper_candidates: list[date] = []

    if application_record is not None:
        lower_candidates.append(application_record.application_date - timedelta(days=LOOKBACK_BEFORE_APPLICATION_DAYS))

    if ipo_record is not None:
        period_start = _period_start_date(ipo_record.donem)
        if period_start is not None:
            lower_candidates.append(period_start - timedelta(days=LOOKBACK_BEFORE_PERIOD_DAYS))
        if ipo_record.borsada_islem_gorme_tarihi is not None:
            upper_candidates.append(ipo_record.borsada_islem_gorme_tarihi.date() + timedelta(days=LOOKAHEAD_AFTER_LISTING_DAYS))

    upper = min(max(upper_candidates), reference_date) if upper_candidates else reference_date
    lower = min(lower_candidates) if lower_candidates else upper - timedelta(days=MAX_WINDOW_DAYS)
    lower = max(lower, upper - timedelta(days=MAX_WINDOW_DAYS))
    return lower, upper


def reprocess_backfilled_disclosures(
    record_id: str,
    cache: BackfillCache,
    *,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    pdf_cache: PdfCache,
    ocr_scanned: bool = False,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> list[KapDisclosure]:
    """Re-materialize whatever an earlier :func:`search_and_backfill`
    call already found for ``record_id`` — never performs a fresh
    historical KAP disclosure-list search itself (a cache miss just
    means nothing has been backfilled for this company yet). The PDF is
    already in ``pdf_cache``, so this only ever re-reads it locally plus
    one small, already-standard live attachment-metadata call per
    disclosure — safe to call on every run."""
    entry = cache.get(record_id)
    if entry is None or not entry.seeds:
        return []
    return [
        process_disclosure_documents(
            seed.to_disclosure(),
            config=config,
            client=client,
            cache=pdf_cache,
            cache_only=True,
            ocr_scanned=ocr_scanned,
            ocr_config=ocr_config,
            ocr_cache=ocr_cache,
        )
        for seed in entry.seeds
    ]


def merge_backfilled_disclosures(
    processed_disclosures: list[KapDisclosure],
    *,
    ipo_records: Sequence[SpkIpoRecord] = (),
    application_records: Sequence[SpkIpoApplicationRecord] = (),
    backfill_cache: BackfillCache,
    pdf_cache: PdfCache,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    ocr_scanned: bool = False,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> list[KapDisclosure]:
    """``processed_disclosures`` plus every previously backfilled
    disclosure for every company either already present in it or named
    by ``ipo_records``/``application_records`` — the cheap, network-
    search-free enrichment step every consumer script (analyze/send/
    validate) should run right before
    :func:`halka_arz_advisor.decision.pipeline.compute_decision_results`,
    so a fresh historical search from an earlier
    ``scripts/backfill_kap_history.py`` run actually reaches decision
    scoring."""
    record_ids = {d.matched_spk_record_id for d in processed_disclosures if d.matched_spk_record_id}
    record_ids |= {ipo_identity(r) for r in ipo_records}
    record_ids |= {application_identity(r) for r in application_records}

    extra: list[KapDisclosure] = []
    for record_id in sorted(record_ids):
        extra.extend(
            reprocess_backfilled_disclosures(
                record_id,
                backfill_cache,
                config=config,
                client=client,
                pdf_cache=pdf_cache,
                ocr_scanned=ocr_scanned,
                ocr_cache=ocr_cache,
                ocr_config=ocr_config,
            )
        )
    return processed_disclosures + extra


# The KAP disclosure-list endpoint has only ever been confirmed (see
# kap.client's module docstring) against ranges up to ~30 days — a
# single call across a wide lifecycle window (months) was observed
# live to fail outright (HTTP 500, likely a server-side range/size
# limit) rather than just being slow. The historical search is
# therefore chunked into calls this size, oldest first, stopping early
# once every wanted type has been found.
_SEARCH_CHUNK_DAYS = 30


def _date_chunks(window_from: date, window_to: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    start = window_from
    while start <= window_to:
        end = min(start + timedelta(days=chunk_days - 1), window_to)
        chunks.append((start, end))
        start = end + timedelta(days=1)
    return chunks


def _search_historical_disclosures(
    record_id: str,
    types_to_find: Sequence[DocumentType],
    window_from: date,
    window_to: date,
    *,
    ipo_records: Sequence[SpkIpoRecord],
    application_records: Sequence[SpkIpoApplicationRecord],
    kap_client: KapClient,
    config: ProbeConfig | None,
    client: httpx.Client | None,
    pdf_cache: PdfCache,
    ocr_scanned: bool,
    ocr_cache: OcrCache | None,
    ocr_config: OcrConfig | None,
) -> list[KapDisclosure]:
    """A chunked, bounded-range KAP disclosure-list search over
    ``[window_from, window_to]``, filtered client-side to ``record_id``
    — reusing :func:`~halka_arz_advisor.kap.matching.match_disclosure`
    unmodified, so an ambiguous historical disclosure (matches more than
    one SPK record) is left unmatched exactly like it would be in the
    normal recent-window flow, never guessed at here.

    Stops searching a document type as soon as one readable disclosure
    of it is found — except ``approved_prospectus``, which (see
    :func:`~halka_arz_advisor.kap.classification.classify_prospectus_document_role`)
    is routinely filed as several separate disclosures (multi-part
    splits, later whole-document corrections) interleaved with unrelated
    exhibits under the very same KAP classification. For that type, an
    exhibit-role candidate is skipped without even being fetched (cheap
    — the role is decided from ``summary``/``title`` alone), and finding
    one base-document-role hit doesn't immediately end the search: every
    real bundle observed live was filed within a single ~30-day chunk
    (occasionally with a correction a few days later), so the search
    instead keeps going until one whole chunk contributes no *new*
    base-document hit — bounding the extra cost to at most one trailing
    chunk beyond the bundle's own, rather than scanning the entire
    (up to 400-day) window unconditionally."""
    if window_from > window_to:
        return []

    still_wanted = set(types_to_find)
    found: list[KapDisclosure] = []
    seen_disclosure_ids: set[str] = set()
    prospectus_hits_total = 0

    for chunk_from, chunk_to in _date_chunks(window_from, window_to, _SEARCH_CHUNK_DAYS):
        if not still_wanted:
            break
        candidates = kap_client.fetch_disclosures(chunk_from, chunk_to)
        prospectus_hits_this_chunk = 0
        for disclosure in candidates:
            if disclosure.document_type not in still_wanted or disclosure.disclosure_id in seen_disclosure_ids:
                continue
            is_prospectus = disclosure.document_type == "approved_prospectus"
            if is_prospectus and classify_prospectus_document_role(disclosure.summary, disclosure.title) == "attachment":
                continue
            matched = match_disclosure(disclosure, ipo_records=ipo_records, application_records=application_records)
            if matched.matched_spk_record_id != record_id:
                continue
            seen_disclosure_ids.add(disclosure.disclosure_id)
            processed = process_disclosure_documents(
                matched,
                config=config,
                client=client,
                cache=pdf_cache,
                cache_only=False,
                ocr_scanned=ocr_scanned,
                ocr_config=ocr_config,
                ocr_cache=ocr_cache,
            )
            found.append(processed)
            readable = processed.pdf_status == "ok" or processed.ocr_status in ("ocr_ok", "ocr_partial")
            if is_prospectus:
                if readable:
                    prospectus_hits_this_chunk += 1
                    prospectus_hits_total += 1
            elif readable:
                still_wanted.discard(disclosure.document_type)

        if "approved_prospectus" in still_wanted and prospectus_hits_total > 0 and prospectus_hits_this_chunk == 0:
            still_wanted.discard("approved_prospectus")

    return found


def search_and_backfill(
    record_id: str,
    *,
    ipo_record: SpkIpoRecord | None,
    application_record: SpkIpoApplicationRecord | None,
    current_disclosures: Sequence[KapDisclosure],
    ipo_records: Sequence[SpkIpoRecord],
    application_records: Sequence[SpkIpoApplicationRecord],
    cache: BackfillCache,
    kap_client: KapClient,
    pdf_cache: PdfCache,
    config: ProbeConfig | None = None,
    client: httpx.Client | None = None,
    ocr_scanned: bool = False,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
    reference_date: date | None = None,
) -> BackfillOutcome:
    """Backfill one matched company: reprocess whatever was already
    found, then — only if some of the five supported document types are
    still missing *and* the relevant lifecycle window hasn't already
    been exhaustively searched — run one bounded historical KAP search
    and persist the outcome so a later run never repeats it.

    ``current_disclosures`` should be exactly the disclosures already
    matched to ``record_id`` from the normal recent-window fetch (not
    yet including backfill) — this reprocesses and folds in whatever
    backfill already knows about before deciding what's still missing.
    """
    ref = reference_date or date.today()
    entry = cache.get(record_id)

    reprocessed = reprocess_backfilled_disclosures(
        record_id, cache, config=config, client=client, pdf_cache=pdf_cache,
        ocr_scanned=ocr_scanned, ocr_cache=ocr_cache, ocr_config=ocr_config,
    )

    combined = list(current_disclosures) + reprocessed
    missing = missing_document_types(combined)
    still_missing = tuple(t for t in missing if entry is None or t not in entry.exhausted_document_types)

    if not still_missing:
        return BackfillOutcome(record_id, tuple(reprocessed), (), False, None)

    window_from, window_to = lifecycle_window(ipo_record=ipo_record, application_record=application_record, reference_date=ref)

    already_covered = entry is not None and entry.searched_from <= window_from and entry.searched_to >= window_to
    if already_covered:
        return BackfillOutcome(record_id, tuple(reprocessed), (), False, (window_from, window_to))

    found = _search_historical_disclosures(
        record_id, still_missing, window_from, window_to,
        ipo_records=ipo_records, application_records=application_records,
        kap_client=kap_client, config=config, client=client, pdf_cache=pdf_cache,
        ocr_scanned=ocr_scanned, ocr_cache=ocr_cache, ocr_config=ocr_config,
    )

    recovered_types = tuple(sorted({d.document_type for d in found if _satisfies_document_type(d)}))
    exhausted = tuple(sorted(set(entry.exhausted_document_types if entry else ()) | (set(still_missing) - set(recovered_types))))
    new_seeds = tuple(
        BackfillDisclosureSeed.from_disclosure(d) for d in found if d.matched_spk_record_id == record_id
    )
    all_seeds = tuple(entry.seeds if entry else ()) + new_seeds
    new_searched_from = min(window_from, entry.searched_from) if entry else window_from
    new_searched_to = max(window_to, entry.searched_to) if entry else window_to

    cache.put(
        record_id,
        BackfillEntry(
            record_id=record_id,
            searched_from=new_searched_from,
            searched_to=new_searched_to,
            exhausted_document_types=exhausted,
            seeds=all_seeds,
            updated_at=datetime.now(UTC),
        ),
    )

    return BackfillOutcome(record_id, tuple(reprocessed + found), recovered_types, True, (window_from, window_to))
