import json

from halka_arz_advisor.probe.models import ProbeResult
from halka_arz_advisor.probe.report import write_json_report, write_markdown_report

RESULTS = [
    ProbeResult(
        source_name="ok_source",
        requested_url="https://example.invalid/a",
        checked_at_utc="2026-08-06T10:00:00Z",
        final_url="https://example.invalid/a",
        http_status=200,
        content_type="text/html",
        response_size_bytes=1234,
        elapsed_ms=456.7,
        page_title="Example Page",
        detected_tables=1,
        detected_links=5,
        possible_download_links=["https://example.invalid/a/data.xlsx"],
        parsing_notes=[],
    ),
    ProbeResult(
        source_name="broken_source",
        requested_url="https://example.invalid/b",
        checked_at_utc="2026-08-06T10:00:05Z",
        error="ConnectError: boom",
    ),
]


def test_write_json_report(tmp_path):
    path = write_json_report(RESULTS, tmp_path, "20260806T100000Z")
    payload = json.loads(path.read_text())
    assert payload["source_count"] == 2
    assert payload["results"][0]["source_name"] == "ok_source"
    assert payload["results"][1]["error"] == "ConnectError: boom"


def test_write_markdown_report(tmp_path):
    path = write_markdown_report(RESULTS, tmp_path, "20260806T100000Z")
    text = path.read_text()
    assert "ok_source" in text
    assert "broken_source" in text
    assert "Example Page" in text
    assert "ConnectError: boom" in text
    assert "data.xlsx" in text
