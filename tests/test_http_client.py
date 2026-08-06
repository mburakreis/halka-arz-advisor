import httpx
import pytest

from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.probe.http_client import build_client, fetch_with_retry

URL = "https://example.invalid/probe"


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=2, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def test_success_on_first_try(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=200, text="ok")
    config = fast_config()
    with build_client(config) as client:
        response = fetch_with_retry(client, URL, config)
    assert response.status_code == 200


def test_retries_on_500_then_succeeds(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=500)
    httpx_mock.add_response(url=URL, status_code=500)
    httpx_mock.add_response(url=URL, status_code=200, text="ok")
    config = fast_config(max_retries=2)
    with build_client(config) as client:
        response = fetch_with_retry(client, URL, config)
    assert response.status_code == 200
    assert len(httpx_mock.get_requests()) == 3


def test_retries_on_429(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=429)
    httpx_mock.add_response(url=URL, status_code=200, text="ok")
    config = fast_config(max_retries=1)
    with build_client(config) as client:
        response = fetch_with_retry(client, URL, config)
    assert response.status_code == 200


def test_does_not_retry_on_404(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=404)
    config = fast_config(max_retries=3)
    with build_client(config) as client:
        response = fetch_with_retry(client, URL, config)
    assert response.status_code == 404
    assert len(httpx_mock.get_requests()) == 1


def test_exhausts_retries_and_returns_last_failing_status(httpx_mock):
    for _ in range(3):
        httpx_mock.add_response(url=URL, status_code=503)
    config = fast_config(max_retries=2)
    with build_client(config) as client:
        response = fetch_with_retry(client, URL, config)
    assert response.status_code == 503
    assert len(httpx_mock.get_requests()) == 3


def test_transport_error_retried_then_raised(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    config = fast_config(max_retries=1)
    with build_client(config) as client:
        with pytest.raises(httpx.ConnectError):
            fetch_with_retry(client, URL, config)
    assert len(httpx_mock.get_requests()) == 2
