"""Writers for the normalized JSON report and the human-readable Markdown report."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ProbeResult


def write_json_report(
    results: list[ProbeResult], out_dir: Path, run_timestamp: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"probe-report-{run_timestamp}.json"
    payload = {
        "run_timestamp_utc": run_timestamp,
        "source_count": len(results),
        "results": [r.to_dict() for r in results],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_markdown_report(
    results: list[ProbeResult], out_dir: Path, run_timestamp: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"probe-report-{run_timestamp}.md"

    lines = [
        f"# Source probe report — {run_timestamp}",
        "",
        "| Source | Status | HTTP | Elapsed (ms) | Tables | Links | Downloads | Title |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        status = "OK" if r.ok else "FAIL"
        title = (r.page_title or "").replace("|", "\\|")
        if len(title) > 60:
            title = title[:57] + "..."
        elapsed = f"{r.elapsed_ms:.0f}" if r.elapsed_ms is not None else "-"
        lines.append(
            f"| {r.source_name} | {status} | {r.http_status or '-'} | {elapsed} | "
            f"{r.detected_tables} | {r.detected_links} | {len(r.possible_download_links)} | {title} |"
        )

    lines.append("")
    lines.append("## Details")
    for r in results:
        lines.append("")
        lines.append(f"### {r.source_name}")
        lines.append(f"- Requested URL: {r.requested_url}")
        lines.append(f"- Final URL: {r.final_url or '-'}")
        lines.append(f"- Content-Type: {r.content_type or '-'}")
        lines.append(f"- Response size: {r.response_size_bytes if r.response_size_bytes is not None else '-'} bytes")
        if r.error:
            lines.append(f"- **Error**: {r.error}")
        if r.parsing_notes:
            lines.append("- Parsing notes:")
            for note in r.parsing_notes:
                lines.append(f"  - {note}")
        if r.possible_download_links:
            lines.append("- Possible download/API links:")
            for link in r.possible_download_links[:20]:
                lines.append(f"  - {link}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
