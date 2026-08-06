"""The stored result of one Gemini analysis run for one matched IPO
(one SPK record, i.e. one company — not one KAP disclosure; see
:mod:`halka_arz_advisor.gemini.analysis`, which aggregates a company's
matched disclosures before analyzing).

``llm_status``/``llm_analysis`` describe Gemini's own narrative-only
output (see :mod:`halka_arz_advisor.gemini.schema` — as of schema
version 2, that no longer includes a signal or confidence at all).
``decision_result`` is the actual source of truth for the signal, the
scores, and the confidence — computed deterministically by
:mod:`halka_arz_advisor.decision.engine` *before* Gemini is ever called,
independent of whether Gemini succeeds, fails validation, or has no
document text to work from at all (see
:mod:`halka_arz_advisor.notify.analysis_formatting`, which reads
``decision_result`` for every number it displays and falls back to
:func:`halka_arz_advisor.decision.explain.format_explanation` for the
narrative portion whenever ``llm_status != "completed"``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..decision.engine import DecisionResult
from .schema import AnalysisOutput

LlmStatus = Literal["pending", "completed", "insufficient_data", "model_unavailable", "invalid_output"]


@dataclass(frozen=True, slots=True)
class AnalysisRecord:
    spk_record_id: str
    llm_status: LlmStatus
    llm_model: str | None
    llm_analysis: AnalysisOutput | None
    llm_warnings: tuple[str, ...]
    analyzed_at: datetime

    # The deterministic decision — the source of truth for signal/
    # scores/confidence/rules/warnings, always populated when a
    # decision could be computed at all (independent of llm_status).
    decision_result: DecisionResult | None = None

    # Cache-key components (None when llm_status is "pending"/"model_unavailable"
    # and no attempt to build a prompt was made at all).
    document_content_hash: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None

    # Populated only when llm_status == "invalid_output", for debugging
    # why the model's output didn't validate.
    raw_response: str | None = None
