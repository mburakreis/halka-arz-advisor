import httpx
import pytest

from halka_arz_advisor.ollama.client import OllamaClient
from halka_arz_advisor.ollama.config import OllamaConfig
from halka_arz_advisor.ollama.exceptions import OllamaModelNotFoundError, OllamaResponseError, OllamaUnavailableError

BASE_URL = "http://localhost:11434"


def make_config(**overrides) -> OllamaConfig:
    defaults = dict(base_url=BASE_URL, model="llama3.1:8b", timeout_seconds=5.0)
    defaults.update(overrides)
    return OllamaConfig(**defaults)


# --------------------------------------------------------------------------
# check_available
# --------------------------------------------------------------------------


def test_check_available_returns_version(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/api/version", json={"version": "0.5.1"})

    with OllamaClient(make_config()) as client:
        assert client.check_available() == "0.5.1"


def test_check_available_raises_on_connection_refused(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=f"{BASE_URL}/api/version")

    with OllamaClient(make_config()) as client, pytest.raises(OllamaUnavailableError):
        client.check_available()


# --------------------------------------------------------------------------
# check_model_available
# --------------------------------------------------------------------------


def test_check_model_available_passes_for_pulled_model(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/api/tags", json={"models": [{"name": "llama3.1:8b"}]})

    with OllamaClient(make_config()) as client:
        client.check_model_available()  # does not raise


def test_check_model_available_matches_without_explicit_tag(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/api/tags", json={"models": [{"name": "llama3.1:8b"}]})

    with OllamaClient(make_config(model="llama3.1")) as client:
        client.check_model_available()  # "llama3.1" untagged still matches "llama3.1:8b"


def test_check_model_available_raises_for_missing_model(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/api/tags", json={"models": [{"name": "mistral:latest"}]})

    with OllamaClient(make_config(model="llama3.1:8b")) as client, pytest.raises(OllamaModelNotFoundError):
        client.check_model_available()


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------


def test_generate_returns_raw_response_string(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/api/generate", json={"model": "llama3.1:8b", "response": '{"a": 1}', "done": True})

    with OllamaClient(make_config()) as client:
        assert client.generate("prompt text", format_schema={"type": "object"}) == '{"a": 1}'


def test_generate_raises_on_http_error(httpx_mock):
    httpx_mock.add_response(url=f"{BASE_URL}/api/generate", status_code=500, text="internal error")

    with OllamaClient(make_config()) as client, pytest.raises(OllamaResponseError):
        client.generate("prompt text", format_schema={"type": "object"})
