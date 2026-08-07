"""Discover an issuer's own official PDF document links from its
investor-relations/IPO page.

Deterministic only: same-domain link filtering plus substring matching
on link text via
:func:`halka_arz_advisor.kap.classification.classify_issuer_link_title`
— no LLM, no fuzzy matching, nothing beyond what the page itself states.
A discovered link whose text doesn't classify to one of the five
issuer_ir-supported types is kept out of the result entirely (never
ingested "just in case").
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..kap.classification import DocumentType, classify_issuer_link_title
from ..probe.config import ProbeConfig
from ..probe.http_client import build_client, fetch_with_retry
from .registry import IssuerIrSource

# The five document types halka_arz_advisor.issuer_ir recovers — a
# subset/superset mix of kap.classification.TARGET_DOCUMENT_TYPES:
# ipo_results/trading_start are dropped (post-listing KAP-only
# regulatory disclosures, never restated on an issuer's own pre-listing
# IPO page), and financial_statement_attachment/use_of_proceeds_report
# are added (standalone attachments an issuer's own site links directly
# — see kap.classification's module docstring for why KAP itself never
# classifies a disclosure as either).
SUPPORTED_ISSUER_IR_DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    "approved_prospectus",
    "investor_sale_announcement",
    "price_determination_report",
    "financial_statement_attachment",
    "use_of_proceeds_report",
)


@dataclass(frozen=True, slots=True)
class DiscoveredLink:
    url: str
    link_text: str
    document_type: DocumentType


def _is_same_or_subdomain(hostname: str, allowed_domain: str) -> bool:
    hostname = hostname.lower()
    allowed_domain = allowed_domain.lower()
    return hostname == allowed_domain or hostname.endswith(f".{allowed_domain}")


def discover_pdf_links(html: str, page_url: str, allowed_domain: str) -> tuple[DiscoveredLink, ...]:
    """Every same-domain PDF link on ``html`` (fetched from ``page_url``)
    whose visible link text classifies to one of
    :data:`SUPPORTED_ISSUER_IR_DOCUMENT_TYPES` — a link to another
    domain, or one that classifies to ``"other"``, is silently excluded,
    not just left unclassified. Deduplicated by resolved URL (a page
    that links the same PDF twice keeps only the first occurrence's
    link text)."""
    soup = BeautifulSoup(html, "html.parser")
    seen_urls: set[str] = set()
    discovered: list[DiscoveredLink] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.lower().startswith("javascript:") or href.startswith("#"):
            continue
        if ".pdf" not in href.lower():
            continue

        resolved_url = urljoin(page_url, href)
        if resolved_url in seen_urls:
            continue

        hostname = urlparse(resolved_url).hostname
        if not hostname or not _is_same_or_subdomain(hostname, allowed_domain):
            continue

        link_text = anchor.get_text(strip=True)
        document_type = classify_issuer_link_title(link_text)
        if document_type not in SUPPORTED_ISSUER_IR_DOCUMENT_TYPES:
            continue

        seen_urls.add(resolved_url)
        discovered.append(DiscoveredLink(url=resolved_url, link_text=link_text, document_type=document_type))

    return tuple(discovered)


def fetch_issuer_ir_page(
    source: IssuerIrSource, *, config: ProbeConfig | None = None, client: httpx.Client | None = None
) -> str | None:
    """The IPO page's raw HTML, or ``None`` if it couldn't be fetched at
    all (unreachable host, transport error, non-2xx, non-HTML response)
    — a crawl failure is never fatal to the caller; it just means
    nothing new is discovered this run (see
    :func:`halka_arz_advisor.issuer_ir.ingest.search_and_ingest`)."""
    cfg = config or ProbeConfig()
    owns_client = client is None
    http_client = client or build_client(cfg)
    try:
        try:
            response = fetch_with_retry(http_client, source.ipo_page_url, cfg)
        except httpx.TransportError:
            return None
        if response.status_code >= 400:
            return None
        content_type = response.headers.get("content-type") or ""
        if "html" not in content_type.lower():
            return None
        return response.text
    finally:
        if owns_client:
            http_client.close()
