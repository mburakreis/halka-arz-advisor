"""The fixed set of public official sources probed in Phase 0."""

from __future__ import annotations

from .models import Source

SOURCES: tuple[Source, ...] = (
    Source(
        name="spk_ipo_data",
        url="https://spk.gov.tr/ihrac-verileri/ilk-halka-arz-verileri",
        purpose="SPK initial public offering (halka arz) issuance data",
    ),
    Source(
        name="spk_ipo_applications",
        url="https://spk.gov.tr/istatistikler/basvurular/ilk-halka-arz-basvurusu",
        purpose="SPK IPO application list / statistics",
    ),
    Source(
        name="spk_web_service_docs",
        url="https://ws.spk.gov.tr/help/index.html",
        purpose="SPK web service (API) documentation",
    ),
    Source(
        name="kap_disclosure_search",
        url="https://kap.org.tr/tr/bildirim-sorgu",
        purpose="KAP (Public Disclosure Platform) disclosure search",
    ),
)
