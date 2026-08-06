"""Data models for the source-probing CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True, slots=True)
class Source:
    """A public data source to probe."""

    name: str
    url: str
    purpose: str


@dataclass(slots=True)
class ProbeResult:
    """Outcome of probing a single source."""

    source_name: str
    requested_url: str
    checked_at_utc: str
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    response_size_bytes: int | None = None
    elapsed_ms: float | None = None
    page_title: str | None = None
    detected_tables: int = 0
    detected_links: int = 0
    possible_download_links: list[str] = field(default_factory=list)
    parsing_notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.http_status is not None and self.http_status < 400

    def to_dict(self) -> dict:
        return asdict(self)
