"""Local Ollama analysis layer: turns a matched IPO's deterministic
extracted facts and cached KAP PDF text into a structured Turkish
decision-support summary via a locally-running Ollama model.

Never downloads a KAP document itself (reads only from the existing
:mod:`halka_arz_advisor.kap.pdf` cache), never sends a whole prospectus
to the model (bounded, page-aware, category-tagged context sections
only — see :mod:`halka_arz_advisor.ollama.context`), and never invents
facts the deterministic extraction didn't find (the model is instructed
to leave them "bilinmiyor" / report ``insufficient_data``).

No OCR, no scoring formula, no Telegram/GitHub Actions integration here.
"""

from .analysis import analyze_company, compute_document_content_hash, verify_ollama_ready
from .cache import AnalysisCache, compute_cache_key
from .client import OllamaClient
from .config import OllamaConfig, load_ollama_config_from_env
from .context import ContextSection, select_context_sections
from .exceptions import (
    OllamaConfigError,
    OllamaError,
    OllamaModelNotFoundError,
    OllamaOutputError,
    OllamaResponseError,
    OllamaUnavailableError,
)
from .models import AnalysisRecord, LlmStatus
from .schema import ANALYSIS_JSON_SCHEMA, PARTICIPATION_SIGNAL_VALUES, AnalysisOutput, SourceReference

__all__ = [
    "analyze_company",
    "verify_ollama_ready",
    "compute_document_content_hash",
    "AnalysisCache",
    "compute_cache_key",
    "OllamaClient",
    "OllamaConfig",
    "load_ollama_config_from_env",
    "ContextSection",
    "select_context_sections",
    "AnalysisRecord",
    "LlmStatus",
    "AnalysisOutput",
    "SourceReference",
    "ANALYSIS_JSON_SCHEMA",
    "PARTICIPATION_SIGNAL_VALUES",
    "OllamaError",
    "OllamaConfigError",
    "OllamaUnavailableError",
    "OllamaModelNotFoundError",
    "OllamaResponseError",
    "OllamaOutputError",
]
