"""Gemini-backed analysis layer: turns a matched IPO's deterministic
extracted facts and cached KAP PDF text into a structured Turkish
decision-support summary via the Gemini API (``google-genai`` SDK).

Never downloads a KAP document itself (reads only from the existing
:mod:`halka_arz_advisor.kap.pdf` cache), never sends a whole prospectus
to the model (bounded, page-aware, category-tagged context sections
only — see :mod:`halka_arz_advisor.gemini.context`), and never invents
facts the deterministic extraction didn't find (the model is instructed
to leave them "bilinmiyor" / report ``insufficient_data``).

No OCR, no scoring formula, no Telegram/GitHub Actions wiring in this
package itself — the GitHub Actions integration lives in the workflow
YAML and calls the same CLI script used locally.
"""

from .analysis import analyze_company, compute_document_content_hash, lookup_analysis, verify_gemini_ready
from .cache import AnalysisCache, compute_cache_key
from .client import GeminiClient
from .config import GeminiConfig, load_gemini_config_from_env
from .context import ContextSection, select_context_sections
from .exceptions import (
    GeminiConfigError,
    GeminiError,
    GeminiModelNotFoundError,
    GeminiOutputError,
    GeminiResponseError,
    GeminiUnavailableError,
)
from .models import AnalysisRecord, LlmStatus
from .schema import ANALYSIS_JSON_SCHEMA, PARTICIPATION_SIGNAL_VALUES, AnalysisOutput, SourceReference

__all__ = [
    "analyze_company",
    "lookup_analysis",
    "verify_gemini_ready",
    "compute_document_content_hash",
    "AnalysisCache",
    "compute_cache_key",
    "GeminiClient",
    "GeminiConfig",
    "load_gemini_config_from_env",
    "ContextSection",
    "select_context_sections",
    "AnalysisRecord",
    "LlmStatus",
    "AnalysisOutput",
    "SourceReference",
    "ANALYSIS_JSON_SCHEMA",
    "PARTICIPATION_SIGNAL_VALUES",
    "GeminiError",
    "GeminiConfigError",
    "GeminiUnavailableError",
    "GeminiModelNotFoundError",
    "GeminiResponseError",
    "GeminiOutputError",
]
