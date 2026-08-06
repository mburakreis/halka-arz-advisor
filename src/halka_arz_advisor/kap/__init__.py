"""KAP (Kamuyu Aydınlatma Platformu / Public Disclosure Platform) ingestion.

Fetches recent disclosures, classifies IPO-related ones by title, matches
them to existing SPK records (completed IPOs and applications), resolves
their official attachments, and — for the approved prospectus and
investor sale announcement — downloads, reads, and deterministically
extracts core IPO participation fields from the PDF. No OCR, scoring,
recommendations, or notifications happen here.
"""

from .attachments import KapAttachment, fetch_disclosure_detail, resolve_attachments, select_primary_attachment
from .classification import DocumentType, TARGET_DOCUMENT_TYPES, classify_title, target_document_types
from .client import KapClient, fetch_disclosures_raw
from .documents import aggregate_company_facts, process_disclosure_documents
from .exceptions import KapApiError, KapResponseError, KapSchemaError, KapTransportError
from .extraction import ExtractedFact, ExtractedFacts, FieldObservation, SourceRef
from .matching import match_disclosure, normalize_company_name
from .models import KapDisclosure, parse_disclosure
from .pdf import PdfCache, PdfDocument, PdfPage, PdfStatus

__all__ = [
    "KapClient",
    "fetch_disclosures_raw",
    "KapDisclosure",
    "parse_disclosure",
    "DocumentType",
    "TARGET_DOCUMENT_TYPES",
    "classify_title",
    "target_document_types",
    "match_disclosure",
    "normalize_company_name",
    "KapAttachment",
    "fetch_disclosure_detail",
    "resolve_attachments",
    "select_primary_attachment",
    "PdfCache",
    "PdfDocument",
    "PdfPage",
    "PdfStatus",
    "ExtractedFact",
    "ExtractedFacts",
    "FieldObservation",
    "SourceRef",
    "process_disclosure_documents",
    "aggregate_company_facts",
    "KapApiError",
    "KapTransportError",
    "KapResponseError",
    "KapSchemaError",
]
