"""Orchestrates one company's Gemini analysis: build bounded context from
cached PDFs, check the analysis cache, prompt the model, validate its
structured output (retrying once on invalid JSON/schema/citation
failures), and cache the result.

A transient failure (rate limit, quota, temporary server error — see
:class:`~halka_arz_advisor.gemini.exceptions.GeminiUnavailableError`) is
deliberately *not* caught here and not retried — it propagates so the
caller (the CLI) can skip just this company without caching a bogus
result, leaving it to be picked up again on a later run.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from ..kap.extraction import FIELD_NAMES, ExtractedFacts
from ..kap.models import KapDisclosure
from ..kap.pdf import PdfCache
from .cache import AnalysisCache, compute_cache_key
from .client import GeminiClient
from .context import DEFAULT_MAX_TOTAL_CHARS, ContextSection, select_context_sections
from .exceptions import GeminiOutputError
from .models import AnalysisRecord
from .prompt import PROMPT_VERSION, allowed_source_references, build_prompt
from .schema import ANALYSIS_JSON_SCHEMA, SCHEMA_VERSION, validate_analysis_output

MAX_GENERATE_ATTEMPTS = 2


def verify_gemini_ready(client: GeminiClient) -> None:
    """Pre-flight checks required before any analysis: the API must be
    reachable, and the configured model must actually be available.

    Raises :class:`~halka_arz_advisor.gemini.exceptions.GeminiUnavailableError`
    or :class:`~halka_arz_advisor.gemini.exceptions.GeminiModelNotFoundError`
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


def _insufficient_data_record(spk_record_id: str, model_name: str) -> AnalysisRecord:
    return AnalysisRecord(
        spk_record_id=spk_record_id,
        llm_status="insufficient_data",
        llm_model=model_name,
        llm_analysis=None,
        llm_warnings=("no extractable PDF text available in the cache for this company's documents",),
        analyzed_at=datetime.now(UTC),
    )


def lookup_analysis(
    *,
    spk_record_id: str,
    facts: ExtractedFacts,
    disclosures: list[KapDisclosure],
    pdf_cache: PdfCache,
    analysis_cache: AnalysisCache,
    model_name: str,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> AnalysisRecord | None:
    """Look up the most recently produced analysis for one company
    *without* ever calling Gemini — for tooling (e.g.
    ``scripts/send_pending_analyses.py``) that only wants to know what's
    already been analyzed, not trigger new analysis.

    Mirrors :func:`analyze_company`'s own cache-key derivation exactly,
    so it finds precisely the record a matching :func:`analyze_company`
    call would have produced or reused. Returns ``None`` on a genuine
    cache miss (nothing analyzed yet for this exact input) — as opposed
    to an ``"insufficient_data"`` result, which (matching
    :func:`analyze_company`) is synthesized fresh here too, since that
    status is never itself written to ``analysis_cache``.
    """
    sections = select_context_sections(disclosures, pdf_cache, max_total_chars=max_total_chars)
    if not sections:
        return _insufficient_data_record(spk_record_id, model_name)

    content_hash = compute_document_content_hash(facts=facts, sections=sections)
    cache_key = compute_cache_key(
        document_content_hash=content_hash,
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    return analysis_cache.get(cache_key)


def analyze_company(
    *,
    spk_record_id: str,
    company_name: str,
    ticker: str | None,
    facts: ExtractedFacts,
    disclosures: list[KapDisclosure],
    pdf_cache: PdfCache,
    analysis_cache: AnalysisCache,
    gemini_client: GeminiClient,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> AnalysisRecord:
    """Analyze one company (one matched SPK record).

    Assumes :func:`verify_gemini_ready` has already succeeded for
    ``gemini_client`` — this does not re-check reachability/model
    availability itself, so a transport/rate-limit failure here
    propagates as a genuine (expected-transient) error rather than being
    silently downgraded.

    Reads PDF text purely from ``pdf_cache`` (see
    :mod:`halka_arz_advisor.gemini.context`) — never downloads. If none
    of the company's cached documents have extractable text,
    ``llm_status="insufficient_data"`` is returned without calling Gemini
    at all.
    """
    sections = select_context_sections(disclosures, pdf_cache, max_total_chars=max_total_chars)

    if not sections:
        return _insufficient_data_record(spk_record_id, gemini_client.model_name)

    content_hash = compute_document_content_hash(facts=facts, sections=sections)
    cache_key = compute_cache_key(
        document_content_hash=content_hash,
        model_name=gemini_client.model_name,
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
        # A transport/rate-limit failure here is expected-transient and
        # propagates — only invalid-JSON/schema/citation failures are
        # retried below.
        raw_response_text = gemini_client.generate(prompt, format_schema=ANALYSIS_JSON_SCHEMA)
        try:
            parsed = json.loads(raw_response_text)
            output = validate_analysis_output(parsed, allowed_references=allowed_refs)
            break
        except (json.JSONDecodeError, GeminiOutputError) as exc:
            warnings.append(f"attempt {attempt}: {exc}")
            continue

    if output is None:
        record = AnalysisRecord(
            spk_record_id=spk_record_id,
            llm_status="invalid_output",
            llm_model=gemini_client.model_name,
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
        llm_model=gemini_client.model_name,
        llm_analysis=output,
        llm_warnings=tuple(warnings),
        analyzed_at=datetime.now(UTC),
        document_content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    analysis_cache.put(cache_key, record)
    return record
