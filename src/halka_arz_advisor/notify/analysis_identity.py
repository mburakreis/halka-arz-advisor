"""Stable content hash for deduplicating Gemini analysis notifications.

Mirrors :mod:`halka_arz_advisor.notify.identity`'s role for SPK
records, but keyed on analysis *content* (not just a record identity)
— see :mod:`halka_arz_advisor.notify.analysis_state` — so both "already
sent" and "changed since last sent" reduce to the same check: has this
exact hash been sent before?
"""

from __future__ import annotations

import hashlib
import json

from ..gemini.models import AnalysisRecord


def _analysis_content_key(record: AnalysisRecord) -> str:
    """A canonical string representing exactly what would change the
    Telegram message's content.

    ``llm_status == "completed"`` records carry the full structured
    output — any field changing (a new risk, a different rationale,
    ...) changes this key. ``llm_status == "insufficient_data"`` records
    have no ``llm_analysis`` at all, so the status itself is the entire
    content that could change (e.g. later becoming "completed" once
    documents are cached).
    """
    if record.llm_analysis is not None:
        return json.dumps(record.llm_analysis.as_dict(), sort_keys=True, ensure_ascii=False)
    return record.llm_status


def analysis_notification_hash(
    *, spk_record_id: str, ticker: str | None, model: str, prompt_version: str, record: AnalysisRecord
) -> str:
    """Stable hash over (SPK record ID, ticker, Gemini model, prompt
    version, analysis content) — used both to detect "already sent"
    (this exact hash is already in the sent-state) and "content changed
    since last sent" (a different hash for the same company)."""
    raw = "|".join(
        [
            spk_record_id,
            ticker or "",
            model,
            prompt_version,
            _analysis_content_key(record),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
