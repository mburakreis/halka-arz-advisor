"""Deterministic IPO market-outcome layer: resolves each IPO's real
first trading date from official sources (SPK + KAP, see
:mod:`halka_arz_advisor.ipo_outcomes.trading_start`), pulls its price
history from :mod:`halka_arz_advisor.market_prices`, and computes
trading-observation-based return/drawdown/BIST-relative fields (see
:mod:`halka_arz_advisor.ipo_outcomes.calculations`) into a persistent
:class:`~halka_arz_advisor.ipo_outcomes.models.IpoMarketOutcome`.

Read-only and backtest-oriented: none of this package's own outcome-
building code is imported by, or imports, ``halka_arz_advisor.decision``
(``expert_v0``'s ``engine``/``scoring_config``/``catalog``/``audit``/
``snapshot``/``pipeline``), ``gemini``, or ``notify`` — building an
outcome never changes ``expert_v0`` entry scoring, narration, Telegram
output, or exit rules.

**One deliberate, narrow exception**, added for
:class:`~halka_arz_advisor.decision.subscription_v1.SubscriptionDecisionV1`:
:mod:`halka_arz_advisor.ipo_outcomes.regime` builds a cross-sectional,
point-in-time "how did *other*, already-settled recent IPOs perform"
signal (:class:`~halka_arz_advisor.ipo_outcomes.regime.RecentIpoRegime`)
that ``decision.subscription_v1`` *does* read — see that module's own
docstring for the leakage-safety argument. This still never lets any
IPO's own outcome influence its own decision; it only aggregates other,
already-fully-realized IPOs' results.
"""

from .builder import build_ipo_market_outcome
from .calculations import RETURN_WINDOWS, OutcomeValue
from .models import IpoMarketOutcome
from .regime import RecentIpoRegime, RecentIpoRegimeStatus, build_recent_ipo_regime, load_all_outcomes
from .store import IpoMarketOutcomeStore
from .trading_start import TradingStartResolution, resolve_trading_start_date

__all__ = [
    "build_ipo_market_outcome",
    "build_recent_ipo_regime",
    "load_all_outcomes",
    "RETURN_WINDOWS",
    "OutcomeValue",
    "IpoMarketOutcome",
    "IpoMarketOutcomeStore",
    "RecentIpoRegime",
    "RecentIpoRegimeStatus",
    "TradingStartResolution",
    "resolve_trading_start_date",
]
