"""Orchestrates one company's Ollama analysis: build bounded context from
cached PDFs, check the analysis cache, prompt the model, validate its
structured output (retrying once on invalid JSON/schema/citation
failures), and cache the result.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ..kap.extraction import FIELD_NAMES, ExtractedFacts
from ..kap.models import KapDisclosure
from ..kap.pdf import PdfCache
from .cache import AnalysisCache, compute_cache_key
from .client import OllamaClient
from .context import DEFAULT_MAX_TOTAL_CHARS, ContextSection, select_context_sections
from .exceptions import OllamaOutputError
from .models import AnalysisRecord
from .prompt import PROMPT_VERSION, allowed_source_references, build_prompt
from .schema import ANALYSIS_JSON_SCHEMA, SCHEMA_VERSION, validate_analysis_output

MAX_GENERATE_ATTEMPTS = 2


def verify_ollama_ready(client: OllamaClient) -> None:
    """Pre-flight checks required before any analysis: the server must be
    reachable, and the configured model must actually be pulled.

    Raises :class:`~halka_arz_advisor.ollama.exceptions.OllamaUnavailableError`
    or :class:`~halka_arz_advisor.ollama.exceptions.OllamaModelNotFoundError`
    — callers should treat either as "no analysis can run right now" and
    should not attempt to call :func:`analyze_company`.
    """
    client.check_available()
    client.check_model_available()


def compute_document_content_hash(*, facts: ExtractedFacts, sections: list[ContextSection]) -> str:
    """Stable hash over exactly what's sent to the model — the
    deterministic facts and the selected context sections — so any
    change to either invalidates the cache."""
    facts_payload = {}
    for field_name in FIELD_NAMES:
        fact = getattr(facts, field_name)
        facts_payload[field_name] = {"status": fact.status, "value": fact.value}
    sections_payload = [
        {"disclosure_id": s.disclosure_id, "page_number": s.page_number, "category": s.category, "text": s.text}
        for s in sections
    ]
    canonical = json.dumps(
        {"facts": facts_payload, "sections": sections_payload}, sort_keys=True, default=str, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def analyze_company(
    *,
    spk_record_id: str,
    company_name: str,
    ticker: str | None,
    facts: ExtractedFacts,
    disclosures: list[KapDisclosure],
    pdf_cache: PdfCache,
    analysis_cache: AnalysisCache,
    ollama_client: OllamaClient,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> AnalysisRecord:
    """Analyze one company (one matched SPK record).

    Assumes :func:`verify_ollama_ready` has already succeeded for
    ``ollama_client`` — this does not re-check reachability/model
    availability itself, so a transport failure here propagates as a
    genuine hard error rather than being silently downgraded.

    Reads PDF text purely from ``pdf_cache`` (see
    :mod:`halka_arz_advisor.ollama.context`) — never downloads. If none
    of the company's cached documents have extractable text,
    ``llm_status="insufficient_data"`` is returned without calling Ollama
    at all.
    """
    sections = select_context_sections(disclosures, pdf_cache, max_total_chars=max_total_chars)

    if not sections:
        return AnalysisRecord(
            spk_record_id=spk_record_id,
            llm_status="insufficient_data",
            llm_model=ollama_client.model_name,
            llm_analysis=None,
            llm_warnings=("no extractable PDF text available in the cache for this company's documents",),
            analyzed_at=datetime.now(UTC),
        )

    content_hash = compute_document_content_hash(facts=facts, sections=sections)
    cache_key = compute_cache_key(
        document_content_hash=content_hash,
        model_name=ollama_client.model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )

    cached = analysis_cache.get(cache_key)
    if cached is not None:
        return cached

    prompt = build_prompt(company_name=company_name, ticker=ticker, facts=facts, sections=sections)
    allowed_refs = allowed_source_references(sections)

    warnings: list[str] = []
    raw_response_text: str | None = None
    output = None

    for attempt in range(1, MAX_GENERATE_ATTEMPTS + 1):
        # A transport/response failure here is a hard error and propagates —
        # only invalid-JSON/schema/citation failures are retried below.
        raw_response_text = ollama_client.generate(prompt, format_schema=ANALYSIS_JSON_SCHEMA)
        try:
            parsed = json.loads(raw_response_text)
            output = validate_analysis_output(parsed, allowed_references=allowed_refs)
            break
        except (json.JSONDecodeError, OllamaOutputError) as exc:
            warnings.append(f"attempt {attempt}: {exc}")
            continue

    if output is None:
        record = AnalysisRecord(
            spk_record_id=spk_record_id,
            llm_status="invalid_output",
            llm_model=ollama_client.model_name,
            llm_analysis=None,
            llm_warnings=tuple(warnings),
            analyzed_at=datetime.now(UTC),
            document_content_hash=content_hash,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            raw_response=raw_response_text,
        )
        analysis_cache.put(cache_key, record)
        return record

    record = AnalysisRecord(
        spk_record_id=spk_record_id,
        llm_status="completed",
        llm_model=ollama_client.model_name,
        llm_analysis=output,
        llm_warnings=tuple(warnings),
        analyzed_at=datetime.now(UTC),
        document_content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    analysis_cache.put(cache_key, record)
    return record
