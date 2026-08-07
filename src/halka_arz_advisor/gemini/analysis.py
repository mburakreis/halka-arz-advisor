"""Orchestrates one company's Gemini analysis: build bounded context from
cached PDFs, check the analysis cache, prompt the model, validate its
structured output (retrying once on invalid JSON/schema/citation
failures), and cache the result.

Every call site must supply an already-computed
:class:`~halka_arz_advisor.decision.engine.DecisionResult` — see
:mod:`halka_arz_advisor.decision.pipeline` for how it's assembled from
the same cached KAP/SPK data. Gemini is never asked to decide the
signal/scores/confidence; it only explains the given result (see
:mod:`halka_arz_advisor.gemini.prompt` and, as of schema version 2,
:mod:`halka_arz_advisor.gemini.schema`, which no longer even accepts
those fields in Gemini's own output).

A transient failure (rate limit, quota, temporary server error — see
:class:`~halka_arz_advisor.gemini.exceptions.GeminiUnavailableError`) is
deliberately *not* caught here and not retried — it propagates so the
caller (the CLI) can skip just this company without caching a bogus
result, leaving it to be picked up again on a later run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

from ..decision.engine import DecisionResult, decision_signature
from ..kap.extraction import FIELD_NAMES, ExtractedFacts
from ..kap.models import KapDisclosure
from ..kap.ocr import OcrCache, OcrConfig
from ..kap.pdf import PdfCache
from .cache import AnalysisCache, compute_cache_key
from .client import GeminiClient
from .context import DEFAULT_MAX_TOTAL_CHARS, ContextSection, select_context_sections
from .exceptions import GeminiOutputError
from .grounding import validate_grounding
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


def compute_document_content_hash(
    *, facts: ExtractedFacts, sections: list[ContextSection], decision_result: DecisionResult
) -> str:
    """Stable hash over exactly what's sent to the model — the
    deterministic facts, the selected context sections, and the
    deterministic decision's time-stable signature (see
    :func:`halka_arz_advisor.decision.engine.decision_signature`) — so
    any *material* change to any of the three invalidates the cache.
    Deliberately excludes confidence/freshness drift (already excluded
    by ``decision_signature`` itself), so the Gemini narrative isn't
    re-generated just because a day passed.
    """
    facts_payload = {}
    for field_name in FIELD_NAMES:
        fact = getattr(facts, field_name)
        facts_payload[field_name] = {"status": fact.status, "value": fact.value}
    sections_payload = [
        {"disclosure_id": s.disclosure_id, "page_number": s.page_number, "category": s.category, "text": s.text}
        for s in sections
    ]
    canonical = json.dumps(
        {"facts": facts_payload, "sections": sections_payload, "decision": decision_signature(decision_result)},
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _insufficient_data_record(spk_record_id: str, model_name: str, decision_result: DecisionResult) -> AnalysisRecord:
    return AnalysisRecord(
        spk_record_id=spk_record_id,
        llm_status="insufficient_data",
        llm_model=model_name,
        llm_analysis=None,
        llm_warnings=("no extractable PDF text available in the cache for this company's documents",),
        analyzed_at=datetime.now(UTC),
        decision_result=decision_result,
    )


def lookup_analysis(
    *,
    spk_record_id: str,
    facts: ExtractedFacts,
    disclosures: list[KapDisclosure],
    pdf_cache: PdfCache,
    analysis_cache: AnalysisCache,
    model_name: str,
    decision_result: DecisionResult,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> AnalysisRecord | None:
    """Look up the most recently produced analysis for one company
    *without* ever calling Gemini — for tooling (e.g.
    ``scripts/send_pending_analyses.py``) that only wants to know what's
    already been analyzed, not trigger new analysis.

    Mirrors :func:`analyze_company`'s own cache-key derivation exactly
    (including ``decision_result``, which the caller must compute the
    same way — see :mod:`halka_arz_advisor.decision.pipeline` — for this
    to find the right entry), so it finds precisely the record a
    matching :func:`analyze_company` call would have produced or reused.
    Returns ``None`` on a genuine cache miss (nothing analyzed yet for
    this exact input) — as opposed to an ``"insufficient_data"`` result,
    which (matching :func:`analyze_company`) is synthesized fresh here
    too, since that status is never itself written to ``analysis_cache``.
    The returned record's ``decision_result`` always reflects the
    ``decision_result`` passed in here (freshly computed by the caller),
    never a stale cached one — see :mod:`halka_arz_advisor.gemini.cache`.
    """
    sections = select_context_sections(
        disclosures, pdf_cache, max_total_chars=max_total_chars, ocr_cache=ocr_cache, ocr_config=ocr_config
    )
    if not sections:
        return _insufficient_data_record(spk_record_id, model_name, decision_result)

    content_hash = compute_document_content_hash(facts=facts, sections=sections, decision_result=decision_result)
    cache_key = compute_cache_key(
        document_content_hash=content_hash,
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    cached = analysis_cache.get(cache_key)
    if cached is None:
        return None
    return replace(cached, decision_result=decision_result)


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
    decision_result: DecisionResult,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    ocr_cache: OcrCache | None = None,
    ocr_config: OcrConfig | None = None,
) -> AnalysisRecord:
    """Analyze one company (one matched SPK record).

    ``decision_result`` must be computed by the caller *before* calling
    this (see :mod:`halka_arz_advisor.decision.pipeline`) — Gemini is
    never asked to decide a signal/score/confidence, only to explain
    this already-final result (see :mod:`halka_arz_advisor.gemini.prompt`).

    Assumes :func:`verify_gemini_ready` has already succeeded for
    ``gemini_client`` — this does not re-check reachability/model
    availability itself, so a transport/rate-limit failure here
    propagates as a genuine (expected-transient) error rather than being
    silently downgraded.

    Reads PDF text purely from ``pdf_cache`` (see
    :mod:`halka_arz_advisor.gemini.context`) — never downloads, and
    never runs OCR itself (``ocr_cache`` is a read-only lookup of
    whatever ``fetch_kap_disclosures.py --ocr-scanned`` already cached).
    If none of the company's cached documents have extractable text,
    ``llm_status="insufficient_data"`` is returned without calling Gemini
    at all — ``decision_result`` (deterministic, independent of Gemini)
    is still attached, so a caller always has a usable signal/score even
    when Gemini has nothing to explain from.
    """
    sections = select_context_sections(
        disclosures, pdf_cache, max_total_chars=max_total_chars, ocr_cache=ocr_cache, ocr_config=ocr_config
    )

    if not sections:
        return _insufficient_data_record(spk_record_id, gemini_client.model_name, decision_result)

    content_hash = compute_document_content_hash(facts=facts, sections=sections, decision_result=decision_result)
    cache_key = compute_cache_key(
        document_content_hash=content_hash,
        model_name=gemini_client.model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )

    cached = analysis_cache.get(cache_key)
    if cached is not None:
        return replace(cached, decision_result=decision_result)

    prompt = build_prompt(company_name=company_name, ticker=ticker, facts=facts, sections=sections, decision_result=decision_result)
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
            candidate = validate_analysis_output(parsed, allowed_references=allowed_refs)
            # Only assign to `output` once grounding also passes — a
            # shape-valid but ungrounded candidate from a failed final
            # attempt must never survive the loop as if it were usable.
            validate_grounding(candidate, decision_result)
            output = candidate
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
            decision_result=decision_result,
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
        decision_result=decision_result,
        document_content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    analysis_cache.put(cache_key, record)
    return record
