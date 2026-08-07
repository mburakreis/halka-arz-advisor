"""Small, explicit ticker -> official investor-relations page registry.

Deliberately data, not code — a future issuer is added by appending one
entry here, never by touching :mod:`halka_arz_advisor.issuer_ir.crawler`
or :mod:`halka_arz_advisor.issuer_ir.ingest`. Every entry names the ONE
page confirmed (as of this writing, live) to carry the issuer's own
official IPO document links directly:

- QUICK — https://www.quicksigorta.com/yatirimci-iliskileri/quick-sigorta/halka-arz
  confirmed live: izahname.pdf, tasarruf-sahiplerine-satis-duyurusu.pdf,
  ek5-...-fiyat-tespit-raporu.pdf, several "EK 3" financial-statement and
  "EK 9" fund-usage attachments, all served from assets.quicksigorta.com
  (a subdomain of the page's own domain).
- METEN — https://metgunenerji.com.tr/halka-arz
  confirmed live: MetgunEnerji-izahname.pdf, tasarruf-sahiplerine-satis-
  duyurusu.pdf, ek-5-...-fiyat-tespit-raporu.pdf, an "EK 3" financial-
  statements/audit-report attachment, an "EK 7" fund-usage attachment.
- MASFN — masfenenerji.com's own official domain (confirmed via SPK/KAP
  disclosure text: "izahname ... şirketin internet sitesi olan
  www.masfenenerji.com ... yayımlanmıştır"), but this project's network
  could not reach the site to confirm the exact IPO-page path at the
  time this registry was written (see ingest.py — an unreachable page
  is handled the same as a page with zero matching links: nothing is
  ingested, no error propagates). The path below is this project's best
  good-faith guess, following the same "yatirimci-iliskileri/.../halka-
  arz" URL shape already confirmed for QUICK — correct it here (data
  only, no code change) once reachable and verified.

``allowed_domain`` is the exact registrable domain a discovered PDF link
must be hosted on (itself or a subdomain) to be trusted at all — see
:func:`halka_arz_advisor.issuer_ir.crawler.discover_pdf_links`. A link
to any other domain (a broker's own site, a media summary, an
aggregator) is never ingested, regardless of how its text classifies.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IssuerIrSource:
    ticker: str
    company_name: str
    ipo_page_url: str
    allowed_domain: str


_REGISTRY: tuple[IssuerIrSource, ...] = (
    IssuerIrSource(
        ticker="QUICK",
        company_name="Quick Sigorta A.Ş.",
        ipo_page_url="https://www.quicksigorta.com/yatirimci-iliskileri/quick-sigorta/halka-arz",
        allowed_domain="quicksigorta.com",
    ),
    IssuerIrSource(
        ticker="MASFN",
        company_name="Masfen Enerji A.Ş.",
        ipo_page_url="https://www.masfenenerji.com/yatirimci-iliskileri/halka-arz",
        allowed_domain="masfenenerji.com",
    ),
    IssuerIrSource(
        ticker="METEN",
        company_name="Metgün Enerji Yatırımları A.Ş.",
        ipo_page_url="https://metgunenerji.com.tr/halka-arz",
        allowed_domain="metgunenerji.com.tr",
    ),
)

_BY_TICKER: dict[str, IssuerIrSource] = {source.ticker: source for source in _REGISTRY}


def get_issuer_ir_source(ticker: str | None) -> IssuerIrSource | None:
    """The registered :class:`IssuerIrSource` for ``ticker``, or
    ``None`` if this project has no known official IR page for it yet —
    never a guessed URL."""
    if not ticker:
        return None
    return _BY_TICKER.get(ticker.strip().upper())


def registered_tickers() -> tuple[str, ...]:
    return tuple(source.ticker for source in _REGISTRY)
