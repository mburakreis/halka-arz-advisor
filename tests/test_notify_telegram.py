import httpx
import pytest

from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.notify.telegram import (
    TelegramConfigError,
    TelegramCredentials,
    TelegramSendError,
    load_credentials_from_env,
    send_message,
)

CREDS = TelegramCredentials(bot_token="123:ABC", chat_id="999")
SEND_URL = "https://api.telegram.org/bot123:ABC/sendMessage"


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=2, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def test_send_message_success(httpx_mock):
    httpx_mock.add_response(url=SEND_URL, json={"ok": True, "result": {"message_id": 1}})

    send_message(CREDS, "hello", config=fast_config())

    request = httpx_mock.get_requests()[0]
    assert request.method == "POST"
    body = request.read().decode()
    assert "chat_id=999" in body
    assert "hello" in body


def test_send_message_raises_on_http_error(httpx_mock):
    httpx_mock.add_response(url=SEND_URL, status_code=400, text="Bad Request: chat not found")

    with pytest.raises(TelegramSendError, match="HTTP 400"):
        send_message(CREDS, "hello", config=fast_config())


def test_send_message_raises_when_ok_is_false(httpx_mock):
    httpx_mock.add_response(url=SEND_URL, json={"ok": False, "description": "bot was blocked by the user"})

    with pytest.raises(TelegramSendError, match="ok=false"):
        send_message(CREDS, "hello", config=fast_config())


def test_send_message_retries_on_5xx_then_succeeds(httpx_mock):
    httpx_mock.add_response(url=SEND_URL, status_code=500)
    httpx_mock.add_response(url=SEND_URL, json={"ok": True})

    send_message(CREDS, "hello", config=fast_config(max_retries=2))
    assert len(httpx_mock.get_requests()) == 2


def test_send_message_retries_on_429_then_succeeds(httpx_mock):
    httpx_mock.add_response(url=SEND_URL, status_code=429)
    httpx_mock.add_response(url=SEND_URL, json={"ok": True})

    send_message(CREDS, "hello", config=fast_config(max_retries=2))
    assert len(httpx_mock.get_requests()) == 2


def test_send_message_wraps_transport_error_after_retries_exhausted(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=SEND_URL)
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=SEND_URL)

    with pytest.raises(TelegramSendError) as exc_info:
        send_message(CREDS, "hello", config=fast_config(max_retries=1))
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_load_credentials_from_env_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    creds = load_credentials_from_env()
    assert creds.bot_token == "123:ABC"
    assert creds.chat_id == "999"


def test_load_credentials_from_env_missing_raises(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(TelegramConfigError):
        load_credentials_from_env()
