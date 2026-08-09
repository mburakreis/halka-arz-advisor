"""Leakage-safe, point-in-time aggregation of *other* IPOs' already-
realized short-term outcomes into a small cross-sectional market-regime
signal (:class:`RecentIpoRegime`).

This is a deliberate, narrow exception to this package's own "never
imported by :mod:`halka_arz_advisor.decision`" rule (see
:mod:`halka_arz_advisor.ipo_outcomes`'s module docstring): that rule
exists to stop a *target* IPO's own post-offer result from leaking into
its *own* pre-offer decision, and nothing here weakens it — a target
IPO's own :class:`~halka_arz_advisor.ipo_outcomes.models.IpoMarketOutcome`
must never be passed to :func:`build_recent_ipo_regime` for itself
(``exclude_ticker`` is required precisely to make this structurally
checkable, not just a caller convention), and every *other* IPO
included must have started trading, and had its 5-day BIST-relative
return window fully complete, strictly before the evaluation timestamp
(``as_of``). This is a cross-sectional regime-context feature (how have
*other*, already-settled recent listings performed), the same category
of information a human analyst would read off a financial news site
before subscribing to a new offering — never a use of the target's own
future result.

Deliberately simple and versioned (:data:`REGIME_VERSION`): a plain
median/positive-share read over ``bist_relative_5d``, with fixed,
stated thresholds (:data:`FAVORABLE_POSITIVE_SHARE`/
:data:`UNFAVORABLE_POSITIVE_SHARE`) chosen from plain reasoning ("a
clear majority vs. a clear minority of recent comparable listings held
up over their first week, relative to the index"), never fit against
this project's own outcome data. Too few mature comparable IPOs (below
:data:`MIN_MATURE_IPOS_FOR_REGIME`) reports ``"UNKNOWN"`` rather than a
noisy read from a handful of data points.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Literal

from .models import IpoMarketOutcome
from .store import IpoMarketOutcomeStore

RecentIpoRegimeStatus = Literal["FAVORABLE", "NEUTRAL", "UNFAVORABLE", "UNKNOWN"]

REGIME_VERSION = "recent_ipo_regime_r1"

DEFAULT_LOOKBACK_DAYS = 90

# A trading-day return window (5 trading sessions) always completes
# within this many *calendar* days even across two back-to-back
# weekends plus a holiday or two — a deliberately generous point-in-time
# safety margin for "was this genuinely knowable by as_of", independent
# of when this project's own cache happened to compute the stored
# value (see module docstring).
RETURN_WINDOW_COMPLETION_BUFFER_DAYS = 9

# Below this many mature comparables, a median/share read is noise, not
# signal — report UNKNOWN rather than guess from e.g. one or two IPOs.
MIN_MATURE_IPOS_FOR_REGIME = 3

# A clearly stronger evidence base than the bare minimum above — used
# only to grade evidence quality (see decision.subscription_v1), never
# to change the regime classification itself.
STRONG_EVIDENCE_MATURE_IPO_COUNT = 6

# Reasoned, stated constants — never fit against this project's own
# ipo_outcomes data. A clear majority (>55%) of recent comparable
# listings holding a positive BIST-relative return through their first
# trading week is read as a favorable regime; a clear minority (<45%)
# as unfavorable; the band between as neutral (mixed evidence, not a
# clear read either way).
FAVORABLE_POSITIVE_SHARE = 0.55
UNFAVORABLE_POSITIVE_SHARE = 0.45


@dataclass(frozen=True, slots=True)
class RecentIpoRegime:
    """A cross-sectional read of how *other* recent, already-mature
    IPOs performed over their first trading week, relative to BIST —
    context for the target IPO's own decision, never built from the
    target's own outcome."""

    status: RecentIpoRegimeStatus
    mature_ipo_count: int
    median_bist_relative_return_5d: float | None
    positive_bist_relative_share_5d: float | None
    window_days: int
    as_of: datetime
    included_tickers: tuple[str, ...]
    version: str = REGIME_VERSION


def _is_mature(outcome: IpoMarketOutcome, *, as_of: datetime, lookback_days: int) -> bool:
    """Whether ``outcome``'s own 5-day BIST-relative return was fully,
    verifiably realized strictly before ``as_of``, and falls within the
    recent lookback window — see module docstring for why the calendar
    margin, not just a not-None check, is what actually makes this
    leakage-safe."""
    if outcome.bist_relative_5d is None:
        return False
    start = outcome.resolved_trading_start_date
    if start is None:
        return False
    as_of_date = as_of.date()
    if start >= as_of_date:
        return False
    if (as_of_date - start).days > lookback_days:
        return False
    completion_date = start + timedelta(days=RETURN_WINDOW_COMPLETION_BUFFER_DAYS)
    return completion_date <= as_of_date


def select_mature_outcomes(
    outcomes: Sequence[IpoMarketOutcome],
    *,
    as_of: datetime,
    exclude_ticker: str | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[IpoMarketOutcome, ...]:
    """The same leakage-safe, point-in-time maturity filter
    :func:`build_recent_ipo_regime` uses internally, exposed on its own
    so another consumer that needs the mature outcomes' own raw fields
    (not just this module's median/positive-share aggregate) — e.g.
    :mod:`halka_arz_advisor.decision.subscription_economics`, which
    needs each mature IPO's actual ``return_5d`` to price a plausible
    TL profit/loss, not the BIST-relative figure this module reads for
    its own, different purpose — can reuse the exact same selection
    instead of re-implementing the leakage-safety rules (see module
    docstring)."""
    excluded = exclude_ticker.strip().upper() if exclude_ticker else None
    candidates = [o for o in outcomes if excluded is None or o.ticker.upper() != excluded]
    return tuple(o for o in candidates if _is_mature(o, as_of=as_of, lookback_days=lookback_days))


def build_recent_ipo_regime(
    outcomes: Sequence[IpoMarketOutcome],
    *,
    as_of: datetime,
    exclude_ticker: str | None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> RecentIpoRegime:
    """Pure — no I/O. ``outcomes`` should be whatever cached
    :class:`IpoMarketOutcome` records the caller has on hand (see
    :func:`load_all_outcomes`); ``exclude_ticker`` (the target IPO's
    own ticker) is structurally required so the target's own outcome,
    even if present in ``outcomes``, can never be counted."""
    mature = list(select_mature_outcomes(outcomes, as_of=as_of, exclude_ticker=exclude_ticker, lookback_days=lookback_days))

    if len(mature) < MIN_MATURE_IPOS_FOR_REGIME:
        return RecentIpoRegime(
            status="UNKNOWN",
            mature_ipo_count=len(mature),
            median_bist_relative_return_5d=None,
            positive_bist_relative_share_5d=None,
            window_days=lookback_days,
            as_of=as_of,
            included_tickers=tuple(sorted(o.ticker for o in mature)),
        )

    returns = [o.bist_relative_5d for o in mature if o.bist_relative_5d is not None]
    median_return = median(returns)
    positive_share = sum(1 for r in returns if r > 0) / len(returns)

    if positive_share >= FAVORABLE_POSITIVE_SHARE:
        status: RecentIpoRegimeStatus = "FAVORABLE"
    elif positive_share <= UNFAVORABLE_POSITIVE_SHARE:
        status = "UNFAVORABLE"
    else:
        status = "NEUTRAL"

    return RecentIpoRegime(
        status=status,
        mature_ipo_count=len(mature),
        median_bist_relative_return_5d=median_return,
        positive_bist_relative_share_5d=positive_share,
        window_days=lookback_days,
        as_of=as_of,
        included_tickers=tuple(sorted(o.ticker for o in mature)),
    )


def load_all_outcomes(store: IpoMarketOutcomeStore) -> tuple[IpoMarketOutcome, ...]:
    """Every cached outcome in ``store``'s directory — a small, explicit
    helper since :class:`~halka_arz_advisor.ipo_outcomes.store.IpoMarketOutcomeStore`
    itself is ticker-keyed (``get(ticker)``) with no bulk listing
    method. Cache-only, no network access."""
    tickers = sorted(p.stem for p in store.directory.glob("*.json"))
    outcomes = (store.get(ticker) for ticker in tickers)
    return tuple(o for o in outcomes if o is not None)


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "FAVORABLE_POSITIVE_SHARE",
    "MIN_MATURE_IPOS_FOR_REGIME",
    "REGIME_VERSION",
    "RETURN_WINDOW_COMPLETION_BUFFER_DAYS",
    "STRONG_EVIDENCE_MATURE_IPO_COUNT",
    "UNFAVORABLE_POSITIVE_SHARE",
    "RecentIpoRegime",
    "RecentIpoRegimeStatus",
    "build_recent_ipo_regime",
    "load_all_outcomes",
    "select_mature_outcomes",
]
