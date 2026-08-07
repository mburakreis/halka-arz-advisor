"""Resolves one IPO's actual first trading date.

SPK's own ``IlkHalkaArzVerileriBilgi.borsadaIslemGormeTarihi`` (see
:class:`halka_arz_advisor.spk.models.SpkIpoRecord`) is currently the
*only* reliable date-bearing official source this project has for this:
a genuine second candidate was attempted (Borsa İstanbul's own KAP
"İşlem Görmeye Başlama" disclosure for the company,
``document_type == "trading_start"``, filed under KAP membership
``BORSA İSTANBUL A.Ş.``) and rejected after live verification —
confirmed against four real 2026 IPOs (QUICK, SARAE, MASFN, METEN) that
this disclosure's ``published_at`` is a **variable-lead-time advance
announcement** ("shares will begin trading on [date]"), not the trading
date itself: it consistently precedes the SPK-recorded trading date by
a different number of calendar days for each company (3 days for QUICK
and MASFN, 5 for METEN — not even a fixed offset). The disclosure itself
carries no attachment and no structured date field beyond its own
publish timestamp (confirmed live: ``attachmentCount: 0`` on every
sampled ``trading_start`` disclosure), so there is no cheap way to
recover the *announced* date from it without adding KAP disclosure
detail-page scraping — out of scope for this pass.

Comparing that announcement date against SPK's date as if they were the
same fact would raise a false "conflict" on essentially *every* IPO
(as first observed here), which is worse than not checking at all. So
this module still records the KAP disclosure's own date(s), for
traceability/audit only, but does **not** treat it as a competing
trading-date candidate: :func:`resolve_trading_start_date` only ever
disagrees with itself if SPK's own record is simply missing (no
candidate at all), never fabricating a date. ``conflict`` is kept as a
field on :class:`TradingStartResolution` — always ``False`` today — so
this remains the extension point if a second true date source is added
later (e.g. by resolving the announcement's own stated date from KAP's
detail page), per this project's "preserve a genuine conflict, never
guess" rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ..kap.models import KapDisclosure
from ..spk.models import SpkIpoRecord

TRADING_START_DOCUMENT_TYPE = "trading_start"


@dataclass(frozen=True, slots=True)
class TradingStartResolution:
    ticker: str
    spk_trading_start_date: date | None
    kap_trading_start_announcement_dates: tuple[date, ...]
    resolved_date: date | None
    conflict: bool


def resolve_trading_start_date(
    ticker: str,
    ipo_record: SpkIpoRecord | None,
    disclosures: Sequence[KapDisclosure],
) -> TradingStartResolution:
    """``disclosures`` should already be narrowed to the one matched
    company (any ``matched_spk_record_id``/ticker filtering is the
    caller's job — this function only reads ``document_type`` and
    ``published_at``).

    ``resolved_date`` is simply ``spk_trading_start_date`` today — see
    this module's docstring for why the KAP ``trading_start``
    disclosure's own date is recorded but not used as a second vote.
    """
    spk_date = ipo_record.borsada_islem_gorme_tarihi.date() if ipo_record and ipo_record.borsada_islem_gorme_tarihi else None

    kap_announcement_dates = tuple(
        sorted({d.published_at.date() for d in disclosures if d.document_type == TRADING_START_DOCUMENT_TYPE})
    )

    return TradingStartResolution(
        ticker=ticker,
        spk_trading_start_date=spk_date,
        kap_trading_start_announcement_dates=kap_announcement_dates,
        resolved_date=spk_date,
        conflict=False,
    )
