"""TCMB EVDS (Elektronik Veri Dağıtım Sistemi) market-context data
provider — fully isolated from :mod:`halka_arz_advisor.kap`: no module
in this package imports from ``kap``, and nothing in ``kap`` imports
from here.

Fetches and caches a handful of pinned, verified series (see
:mod:`halka_arz_advisor.evds.registry`) — BIST 100 level and volume,
TCMB's policy rate, BIST TLREF, and TÜİK's headline CPI — and derives
nine deterministic market-context features from them (see
:mod:`halka_arz_advisor.evds.features`). Requires ``EVDS_API_KEY`` in
the environment; degrades gracefully (never raises, never blocks a
caller) when it's absent or the service is unreachable — see
:mod:`halka_arz_advisor.evds.refresh`.
"""

from .cache import EvdsCache
from .config import EvdsConfig, load_evds_config_from_env
from .features import build_market_context_snapshot
from .models import EvdsObservation, MarketContextFeatureValue, MarketContextSnapshot
from .refresh import RefreshOutcome, refresh_market_context
from .registry import EVDS_SERIES_REGISTRY, EvdsSeriesSpec, get_series_spec

__all__ = [
    "EvdsConfig",
    "load_evds_config_from_env",
    "EvdsCache",
    "EvdsObservation",
    "MarketContextFeatureValue",
    "MarketContextSnapshot",
    "EVDS_SERIES_REGISTRY",
    "EvdsSeriesSpec",
    "get_series_spec",
    "build_market_context_snapshot",
    "RefreshOutcome",
    "refresh_market_context",
]
