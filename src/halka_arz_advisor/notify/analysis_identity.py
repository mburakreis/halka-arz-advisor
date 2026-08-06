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

from ..decision.engine import decision_signature
from ..gemini.models import AnalysisRecord


def _analysis_content_key(record: AnalysisRecord) -> str:
    """A canonical string representing exactly what would change the
    Telegram message's content.

    Always folds in the deterministic decision's time-stable signature
    (see :func:`halka_arz_advisor.decision.engine.decision_signature` —
    deliberately excludes confidence/freshness drift, so a routine day
    passing never counts as "changed"), so a materially different
    decision (a new document processed, a resolved conflict, ...)
    always triggers a re-send even if Gemini's own narrative or status
    happens not to change. ``llm_status == "completed"`` records also
    fold in the full structured narrative output (any field changing —
    a new risk, a different explanation, ...) changes this key;
    otherwise the status itself is folded in (e.g. later becoming
    "completed" once documents are cached is a content change).
    """
    decision_part = json.dumps(decision_signature(record.decision_result), sort_keys=True) if record.decision_result else "none"
    if record.llm_analysis is not None:
        analysis_part = json.dumps(record.llm_analysis.as_dict(), sort_keys=True, ensure_ascii=False)
    else:
        analysis_part = record.llm_status
    return f"{decision_part}|{analysis_part}"


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
