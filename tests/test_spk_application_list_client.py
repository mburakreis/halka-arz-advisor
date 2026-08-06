import httpx
import pytest

from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.spk.application_list import APPLICATION_LIST_URL, SpkApplicationListClient
from halka_arz_advisor.spk.exceptions import SpkResponseError, SpkTransportError


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=2, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def test_get_applications_returns_only_valid_typed_records(httpx_mock, fixture_html):
    httpx_mock.add_response(
        url=APPLICATION_LIST_URL, text=fixture_html("spk_application_table_page.html"),
        headers={"content-type": "text/html; charset=utf-8"},
    )

    with SpkApplicationListClient(fast_config()) as client:
        records = client.get_applications()

    assert len(records) == 4
    assert all(r.company_name for r in records)


def test_fetch_applications_exposes_invalid_rows_too(httpx_mock, fixture_html):
    httpx_mock.add_response(
        url=APPLICATION_LIST_URL, text=fixture_html("spk_application_table_page.html"),
        headers={"content-type": "text/html; charset=utf-8"},
    )

    with SpkApplicationListClient(fast_config()) as client:
        result = client.fetch_applications()

    assert len(result.records) == 4
    assert len(result.invalid_rows) == 3


def test_fetch_raw_preserves_html_before_parsing(httpx_mock, fixture_html):
    html = fixture_html("spk_application_table_page.html")
    httpx_mock.add_response(url=APPLICATION_LIST_URL, text=html, headers={"content-type": "text/html; charset=utf-8"})

    with SpkApplicationListClient(fast_config()) as client:
        raw = client.fetch_raw()

    assert raw.http_status == 200
    assert raw.html == html
    assert raw.requested_url == APPLICATION_LIST_URL


def test_rejects_non_html_content_type(httpx_mock):
    httpx_mock.add_response(url=APPLICATION_LIST_URL, json={"unexpected": True}, headers={"content-type": "application/json"})

    with SpkApplicationListClient(fast_config()) as client:
        with pytest.raises(SpkResponseError, match="non-HTML content-type"):
            client.get_applications()


def test_http_error_status_raises_response_error(httpx_mock):
    httpx_mock.add_response(url=APPLICATION_LIST_URL, status_code=503)
    httpx_mock.add_response(url=APPLICATION_LIST_URL, status_code=503)
    httpx_mock.add_response(url=APPLICATION_LIST_URL, status_code=404, text="not found")

    with SpkApplicationListClient(fast_config(max_retries=2)) as client:
        with pytest.raises(SpkResponseError, match="HTTP 404"):
            client.get_applications()


def test_retries_on_5xx_then_succeeds(httpx_mock, fixture_html):
    httpx_mock.add_response(url=APPLICATION_LIST_URL, status_code=500)
    httpx_mock.add_response(
        url=APPLICATION_LIST_URL, text=fixture_html("spk_application_table_page.html"),
        headers={"content-type": "text/html"},
    )

    with SpkApplicationListClient(fast_config(max_retries=2)) as client:
        records = client.get_applications()

    assert len(records) == 4
    assert len(httpx_mock.get_requests()) == 2


def test_transport_error_wrapped_after_retries_exhausted(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=APPLICATION_LIST_URL)
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=APPLICATION_LIST_URL)

    with SpkApplicationListClient(fast_config(max_retries=1)) as client:
        with pytest.raises(SpkTransportError) as exc_info:
            client.get_applications()

    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
