"""Runtime configuration for the source-probing CLI."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_USER_AGENT = (
    "halka-arz-advisor-source-probe/0.1 "
    "(+https://github.com/; contact: burakreis61e@gmail.com; Phase 0 research bot)"
)


@dataclass(frozen=True, slots=True)
class ProbeConfig:
    """Tunables for how the prober talks to remote sources."""

    user_agent: str = DEFAULT_USER_AGENT
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 20.0
    delay_between_requests_seconds: float = 2.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.5
    retry_status_codes: frozenset[int] = frozenset(
        {429, 500, 502, 503, 504}
    )
