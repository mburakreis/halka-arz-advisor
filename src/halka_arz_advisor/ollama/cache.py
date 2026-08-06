"""Disk cache for Ollama analysis results.

Keyed by a hash of ``(document_content_hash, model_name, prompt_version,
schema_version)`` — change any one of those (the input text/facts, the
model, or either version) and the cache key changes, so a second run
with genuinely unchanged inputs is the only case that hits the cache.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .models import AnalysisRecord
from .schema import AnalysisOutput, SourceReference


def compute_cache_key(*, document_content_hash: str, model_name: str, prompt_version: str, schema_version: str) -> str:
    raw = f"{document_content_hash}|{model_name}|{prompt_version}|{schema_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _analysis_output_from_dict(data: dict) -> AnalysisOutput:
    return AnalysisOutput(
        company_summary=data["company_summary"],
        offering_summary=data["offering_summary"],
        use_of_proceeds_summary=data["use_of_proceeds_summary"],
        key_risks=tuple(data["key_risks"]),
        positive_factors=tuple(data["positive_factors"]),
        negative_factors=tuple(data["negative_factors"]),
        missing_information=tuple(data["missing_information"]),
        data_conflicts=tuple(data["data_conflicts"]),
        participation_signal=data["participation_signal"],
        participation_rationale=data["participation_rationale"],
        confidence=data["confidence"],
        source_references=tuple(
            SourceReference(disclosure_id=r["disclosure_id"], page_number=r["page_number"])
            for r in data["source_references"]
        ),
    )


def _record_to_dict(record: AnalysisRecord) -> dict:
    return {
        "spk_record_id": record.spk_record_id,
        "llm_status": record.llm_status,
        "llm_model": record.llm_model,
        "llm_analysis": record.llm_analysis.as_dict() if record.llm_analysis is not None else None,
        "llm_warnings": list(record.llm_warnings),
        "analyzed_at": record.analyzed_at.isoformat(),
        "document_content_hash": record.document_content_hash,
        "prompt_version": record.prompt_version,
        "schema_version": record.schema_version,
        "raw_response": record.raw_response,
    }


def _record_from_dict(data: dict) -> AnalysisRecord:
    return AnalysisRecord(
        spk_record_id=data["spk_record_id"],
        llm_status=data["llm_status"],
        llm_model=data.get("llm_model"),
        llm_analysis=_analysis_output_from_dict(data["llm_analysis"]) if data.get("llm_analysis") else None,
        llm_warnings=tuple(data.get("llm_warnings", [])),
        analyzed_at=datetime.fromisoformat(data["analyzed_at"]),
        document_content_hash=data.get("document_content_hash"),
        prompt_version=data.get("prompt_version"),
        schema_version=data.get("schema_version"),
        raw_response=data.get("raw_response"),
    )


class AnalysisCache:
    """Disk cache for :class:`AnalysisRecord`, one JSON file per cache key."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, cache_key: str) -> Path:
        return self.directory / f"{cache_key}.json"

    def get(self, cache_key: str) -> AnalysisRecord | None:
        path = self._path(cache_key)
        if not path.exists():
            return None
        return _record_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def put(self, cache_key: str, record: AnalysisRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(cache_key).write_text(
            json.dumps(_record_to_dict(record), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def has(self, cache_key: str) -> bool:
        return self._path(cache_key).exists()
