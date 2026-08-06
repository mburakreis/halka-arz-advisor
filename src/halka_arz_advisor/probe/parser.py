"""HTML inspection helpers, used only when the response looks like HTML.

Kept intentionally shallow for Phase 0: we are checking whether a page
is reachable and what shape it has (tables, links, download-looking
hrefs), not extracting IPO data yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

DOWNLOAD_EXTENSIONS = (".pdf", ".xls", ".xlsx", ".csv", ".zip", ".doc", ".docx", ".json")
DOWNLOAD_HINT_KEYWORDS = ("download", "indir", "export", "/api/", "raporlar")

# Some documentation pages (e.g. Swagger/OpenAPI UIs) never put the real
# spec URL in an <a href>; it's embedded as a quoted string inside an
# inline <script> config blob instead. This is still a link the *page
# itself* discloses (not a guessed endpoint), so we scan raw text for
# quoted paths that look like API/spec documents.
INLINE_SPEC_URL_PATTERN = re.compile(
    r"""["']((?:https?://[^"']+|/[^"'\s]*)\.(?:json|xml|wsdl|ya?ml))["']""",
    re.IGNORECASE,
)

# Below this many visible characters in <body>, a page with scripts is
# flagged as possibly relying on client-side rendering.
SHORT_BODY_CHAR_THRESHOLD = 200


@dataclass(slots=True)
class ParsedHtml:
    page_title: str | None = None
    detected_tables: int = 0
    detected_links: int = 0
    possible_download_links: list[str] = field(default_factory=list)
    parsing_notes: list[str] = field(default_factory=list)


def parse_html(html: str, base_url: str) -> ParsedHtml:
    soup = BeautifulSoup(html, "html.parser")

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip() or None

    tables = soup.find_all("table")
    anchors = soup.find_all("a", href=True)

    download_links: set[str] = set()
    for anchor in anchors:
        href = anchor["href"].strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        lower_href = href.lower()
        looks_like_download = lower_href.endswith(DOWNLOAD_EXTENSIONS) or any(
            hint in lower_href for hint in DOWNLOAD_HINT_KEYWORDS
        )
        if looks_like_download:
            download_links.add(urljoin(base_url, href))

    inline_spec_urls: set[str] = set()
    for script in soup.find_all("script"):
        script_text = script.string or ""
        for match in INLINE_SPEC_URL_PATTERN.findall(script_text):
            inline_spec_urls.add(urljoin(base_url, match))
    download_links |= inline_spec_urls

    notes: list[str] = []
    if inline_spec_urls:
        notes.append(
            "found spec/data URL(s) referenced inside inline <script> config "
            "(not an <a href>, not a guess — extracted from the page's own JS): "
            + ", ".join(sorted(inline_spec_urls))
        )
    if not tables:
        notes.append("no <table> elements found in the server-rendered HTML")

    body = soup.body
    visible_text_len = len(body.get_text(strip=True)) if body else 0
    script_count = len(soup.find_all("script"))
    if visible_text_len < SHORT_BODY_CHAR_THRESHOLD and script_count > 0:
        notes.append(
            f"body text is short ({visible_text_len} chars) alongside {script_count} "
            "<script> tag(s); page may depend on client-side rendering to show real content"
        )

    return ParsedHtml(
        page_title=title,
        detected_tables=len(tables),
        detected_links=len(anchors),
        possible_download_links=sorted(download_links),
        parsing_notes=notes,
    )
