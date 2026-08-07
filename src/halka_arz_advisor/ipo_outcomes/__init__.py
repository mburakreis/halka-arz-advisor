"""Deterministic IPO market-outcome layer: resolves each IPO's real
first trading date from official sources (SPK + KAP, see
:mod:`halka_arz_advisor.ipo_outcomes.trading_start`), pulls its price
history from :mod:`halka_arz_advisor.market_prices`, and computes
trading-observation-based return/drawdown/BIST-relative fields (see
:mod:`halka_arz_advisor.ipo_outcomes.calculations`) into a persistent
:class:`~halka_arz_advisor.ipo_outcomes.models.IpoMarketOutcome`.

Read-only and backtest-oriented: nothing here is imported by, or
imports, :mod:`halka_arz_advisor.decision`, ``gemini``, or ``notify`` —
building an outcome never changes entry scoring, narration, Telegram
output, or exit rules.
"""

from .builder import build_ipo_market_outcome
from .calculations import RETURN_WINDOWS, OutcomeValue
from .models import IpoMarketOutcome
from .store import IpoMarketOutcomeStore
from .trading_start import TradingStartResolution, resolve_trading_start_date

__all__ = [
    "build_ipo_market_outcome",
    "RETURN_WINDOWS",
    "OutcomeValue",
    "IpoMarketOutcome",
    "IpoMarketOutcomeStore",
    "TradingStartResolution",
    "resolve_trading_start_date",
]
