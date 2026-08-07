"""``EVDS_API_KEY`` resolution.

Deliberately the only place in this package that reads an environment
variable — every other module takes a resolved :class:`EvdsConfig` (or
``None``) as a plain argument, so the "key is missing" case is handled
once, here, rather than scattered across callers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://evds3.tcmb.gov.tr/igmevdsms-dis"


@dataclass(frozen=True, slots=True)
class EvdsConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL


def load_evds_config_from_env() -> EvdsConfig | None:
    """Returns ``None`` — never raises — when ``EVDS_API_KEY`` is unset
    or blank. Every caller in this package (and
    ``scripts/refresh_evds_market_context.py``) is expected to treat
    ``None`` as "the provider is unavailable right now" and skip the
    refresh rather than fail: cached market-context data must remain
    usable with no key present at all (e.g. a GitHub Actions run
    without the ``EVDS_API_KEY`` secret configured)."""
    api_key = os.environ.get("EVDS_API_KEY", "").strip()
    if not api_key:
        return None
    return EvdsConfig(api_key=api_key)
