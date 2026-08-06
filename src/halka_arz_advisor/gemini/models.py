"""The stored result of one Gemini analysis run for one matched IPO
(one SPK record, i.e. one company — not one KAP disclosure; see
:mod:`halka_arz_advisor.gemini.analysis`, which aggregates a company's
matched disclosures before analyzing)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

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

    # Cache-key components (None when llm_status is "pending"/"model_unavailable"
    # and no attempt to build a prompt was made at all).
    document_content_hash: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None

    # Populated only when llm_status == "invalid_output", for debugging
    # why the model's output didn't validate.
    raw_response: str | None = None
