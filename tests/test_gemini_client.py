"""Tests the GeminiClient wrapper against a hand-built fake standing in
for google.genai.Client (injected via the constructor's ``client=``
param) — this exercises our error-mapping and response-parsing logic
without depending on the real Gemini API's exact wire format."""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors

from halka_arz_advisor.gemini.client import GeminiClient
from halka_arz_advisor.gemini.config import GeminiConfig
from halka_arz_advisor.gemini.exceptions import GeminiModelNotFoundError, GeminiResponseError, GeminiUnavailableError


def make_config(**overrides) -> GeminiConfig:
    defaults = dict(api_key="test-key", model="gemini-3.5-flash", timeout_seconds=5.0)
    defaults.update(overrides)
    return GeminiConfig(**defaults)


class _FakeModels:
    def __init__(self, *, list_error=None, get_result=None, get_error=None, generate_result=None, generate_error=None):
        self._list_error = list_error
        self._get_result = get_result
        self._get_error = get_error
        self._generate_result = generate_result
        self._generate_error = generate_error

    def list(self, config=None):
        if self._list_error:
            raise self._list_error
        return iter([SimpleNamespace(name="models/gemini-3.5-flash")])

    def get(self, *, model):
        if self._get_error:
            raise self._get_error
        return self._get_result or SimpleNamespace(name=f"models/{model}")

    def generate_content(self, *, model, contents, config):
        if self._generate_error:
            raise self._generate_error
        return self._generate_result


class _FakeGenaiClient:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models
        self.closed = False

    def close(self) -> None:
        self.closed = True


def client_error(code: int, message: str) -> genai_errors.ClientError:
    return genai_errors.ClientError(code, {"message": message})


# --------------------------------------------------------------------------
# check_available
# --------------------------------------------------------------------------


def test_check_available_passes_when_reachable():
    fake = _FakeGenaiClient(_FakeModels())
    with GeminiClient(make_config(), client=fake) as client:
        client.check_available()  # does not raise


def test_check_available_raises_on_connection_refused():
    fake = _FakeGenaiClient(_FakeModels(list_error=httpx.ConnectError("connection refused")))
    with GeminiClient(make_config(), client=fake) as client, pytest.raises(GeminiUnavailableError):
        client.check_available()


def test_check_available_raises_unavailable_on_rate_limit():
    fake = _FakeGenaiClient(_FakeModels(list_error=client_error(429, "Rate limit exceeded")))
    with GeminiClient(make_config(), client=fake) as client, pytest.raises(GeminiUnavailableError):
        client.check_available()


# --------------------------------------------------------------------------
# check_model_available
# --------------------------------------------------------------------------


def test_check_model_available_passes_for_available_model():
    fake = _FakeGenaiClient(_FakeModels(get_result=SimpleNamespace(name="models/gemini-3.5-flash")))
    with GeminiClient(make_config(), client=fake) as client:
        client.check_model_available()  # does not raise


def test_check_model_available_raises_for_missing_model():
    fake = _FakeGenaiClient(_FakeModels(get_error=client_error(404, "Model is not found: models/nope")))
    with GeminiClient(make_config(model="nope"), client=fake) as client, pytest.raises(GeminiModelNotFoundError):
        client.check_model_available()


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------


def test_generate_returns_raw_response_string():
    fake = _FakeGenaiClient(_FakeModels(generate_result=SimpleNamespace(text='{"a": 1}')))
    with GeminiClient(make_config(), client=fake) as client:
        assert client.generate("prompt text", format_schema={"type": "object"}) == '{"a": 1}'


def test_generate_raises_unavailable_on_rate_limit():
    fake = _FakeGenaiClient(_FakeModels(generate_error=client_error(429, "Rate limit exceeded")))
    with GeminiClient(make_config(), client=fake) as client, pytest.raises(GeminiUnavailableError):
        client.generate("prompt text", format_schema={"type": "object"})


def test_generate_raises_response_error_on_bad_request():
    fake = _FakeGenaiClient(_FakeModels(generate_error=client_error(400, "Invalid argument")))
    with GeminiClient(make_config(), client=fake) as client, pytest.raises(GeminiResponseError):
        client.generate("prompt text", format_schema={"type": "object"})


def test_generate_raises_unavailable_on_transport_error():
    fake = _FakeGenaiClient(_FakeModels(generate_error=httpx.ConnectError("connection refused")))
    with GeminiClient(make_config(), client=fake) as client, pytest.raises(GeminiUnavailableError):
        client.generate("prompt text", format_schema={"type": "object"})


def test_close_closes_underlying_client():
    fake = _FakeGenaiClient(_FakeModels())
    with GeminiClient(make_config(), client=fake):
        pass
    assert fake.closed is True
