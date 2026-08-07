"""KAP-independent official-document fallback: recover missing IPO
lifecycle documents from an issuer's own investor-relations/IPO page
when KAP is unavailable, rate-limited, or simply hasn't published (or
this project hasn't yet found) a given document type.

A source-agnostic ingestion path on top of the existing KAP pipeline —
:mod:`halka_arz_advisor.kap.pdf` (download+cache), optionally
:mod:`halka_arz_advisor.kap.ocr` (scanned fallback), and
:mod:`halka_arz_advisor.kap.extraction` / :mod:`halka_arz_advisor.kap.financials`
(field/financial extraction) are all reused unmodified. Discovery and
classification are both deterministic substring matching on link
text/title (see :mod:`halka_arz_advisor.kap.classification`) — never an
LLM. ``issuer_ir`` documents carry lower authority than KAP: see
:func:`halka_arz_advisor.kap.extraction.apply_lower_authority_fallback`
for the exact "KAP always wins when both have a value" rule.
"""

from .cache import IngestedIssuerDocument, IssuerIrCache, IssuerIrCacheEntry
from .crawler import SUPPORTED_ISSUER_IR_DOCUMENT_TYPES, DiscoveredLink, discover_pdf_links, fetch_issuer_ir_page
from .ingest import (
    IssuerIrIngestOutcome,
    collect_supplementary_disclosures,
    reprocess_ingested_documents,
    resolve_registered_record_id,
    search_and_ingest,
)
from .registry import IssuerIrSource, get_issuer_ir_source, registered_tickers

__all__ = [
    "IssuerIrSource",
    "get_issuer_ir_source",
    "registered_tickers",
    "DiscoveredLink",
    "SUPPORTED_ISSUER_IR_DOCUMENT_TYPES",
    "discover_pdf_links",
    "fetch_issuer_ir_page",
    "IssuerIrCache",
    "IssuerIrCacheEntry",
    "IngestedIssuerDocument",
    "IssuerIrIngestOutcome",
    "search_and_ingest",
    "reprocess_ingested_documents",
    "resolve_registered_record_id",
    "collect_supplementary_disclosures",
]
