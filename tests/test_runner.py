import json
from pathlib import Path

import httpx
import pytest

from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.probe.models import Source
from halka_arz_advisor.probe.runner import run_all

HTML_URL = "https://example.invalid/table-source"
FAIL_URL = "https://example.invalid/broken-source"

TEST_SOURCES = (
    Source(name="fake_table_source", url=HTML_URL, purpose="test fixture with a table"),
    Source(name="fake_broken_source", url=FAIL_URL, purpose="test fixture that always errors"),
)


def _fast_config() -> ProbeConfig:
    return ProbeConfig(
        max_retries=1,
        backoff_base_seconds=0.001,
        delay_between_requests_seconds=0.001,
    )


def test_run_all_records_html_page_and_saves_raw_files(httpx_mock, tmp_path, fixture_html):
    httpx_mock.add_response(
        url=HTML_URL, status_code=200, text=fixture_html("table_page.html"),
        headers={"content-type": "text/html; charset=utf-8"},
    )
    httpx_mock.add_response(url=FAIL_URL, status_code=404)

    raw_dir = tmp_path / "raw"
    run = run_all(_fast_config(), raw_dir=raw_dir, sources=TEST_SOURCES)

    assert len(run.results) == 2
    ok_result = run.results[0]
    assert ok_result.source_name == "fake_table_source"
    assert ok_result.http_status == 200
    assert ok_result.ok is True
    assert ok_result.detected_tables == 1
    assert ok_result.page_title == "İlk Halka Arz Verileri - SPK"
    assert ok_result.error is None

    fail_result = run.results[1]
    assert fail_result.http_status == 404
    assert fail_result.ok is False
    assert fail_result.error is not None

    raw_files = list((raw_dir / "fake_table_source" / run.run_timestamp).iterdir())
    names = {f.name for f in raw_files}
    assert "meta.json" in names
    assert "response.html" in names

    meta = json.loads((raw_dir / "fake_table_source" / run.run_timestamp / "meta.json").read_text())
    assert meta["http_status"] == 200
    assert meta["requested_url"] == HTML_URL


def test_run_all_records_transport_error_without_crashing(httpx_mock, tmp_path):
    # _fast_config() uses max_retries=1, i.e. 2 attempts per source.
    for url in (HTML_URL, FAIL_URL):
        httpx_mock.add_exception(httpx.ConnectError("dns failure"), url=url)
        httpx_mock.add_exception(httpx.ConnectError("dns failure"), url=url)

    raw_dir = tmp_path / "raw"
    run = run_all(_fast_config(), raw_dir=raw_dir, sources=TEST_SOURCES)

    for result in run.results:
        assert result.ok is False
        assert "ConnectError" in result.error
        assert result.http_status is None

    meta_path = raw_dir / "fake_broken_source" / run.run_timestamp / "meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["error"] is not None
    assert "http_status" not in meta
