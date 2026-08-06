import json

from halka_arz_advisor.notify.check import check_and_notify
from halka_arz_advisor.notify.state import SeenRecordsState, save_state
from halka_arz_advisor.notify.telegram import TelegramCredentials, send_message
from halka_arz_advisor.probe.config import ProbeConfig
from halka_arz_advisor.spk.application_list import APPLICATION_LIST_URL, SpkApplicationListClient
from halka_arz_advisor.spk.client import SpkApiClient

IPO_URL = "https://ws.spk.gov.tr/BorclanmaAraclari/api/IlkHalkaArzVerileri?yil=2024"


def fast_config(**overrides) -> ProbeConfig:
    defaults = dict(max_retries=1, backoff_base_seconds=0.001)
    defaults.update(overrides)
    return ProbeConfig(**defaults)


def _mock_both_sources(httpx_mock, fixture_json, fixture_html):
    httpx_mock.add_response(url=IPO_URL, json=fixture_json("spk_ipo_2024_sample.json"))
    httpx_mock.add_response(
        url=APPLICATION_LIST_URL,
        text=fixture_html("spk_application_table_page.html"),
        headers={"content-type": "text/html; charset=utf-8"},
    )


def test_first_run_records_state_without_notifying(httpx_mock, fixture_json, fixture_html, tmp_path):
    _mock_both_sources(httpx_mock, fixture_json, fixture_html)
    state_path = tmp_path / "seen.json"
    sent: list[str] = []

    with SpkApiClient(fast_config()) as ipo_client, SpkApplicationListClient(fast_config()) as app_client:
        result = check_and_notify(
            ipo_client=ipo_client,
            application_client=app_client,
            state_path=state_path,
            year=2024,
            send_existing=False,
            notifier=sent.append,
        )

    assert result.is_first_run is True
    assert len(result.new_ipo_records) == 2
    assert len(result.new_application_records) == 4
    assert result.notified_ipo_records == []
    assert result.notified_application_records == []
    assert sent == []  # nothing notified on a silent first run

    assert state_path.exists()
    saved = json.loads(state_path.read_text())
    assert len(saved["ipo_identities"]) == 2
    assert len(saved["application_identities"]) == 4
    assert saved["initialized_at_utc"] is not None


def test_first_run_with_send_existing_notifies_everything(httpx_mock, fixture_json, fixture_html, tmp_path):
    _mock_both_sources(httpx_mock, fixture_json, fixture_html)
    state_path = tmp_path / "seen.json"
    sent: list[str] = []

    with SpkApiClient(fast_config()) as ipo_client, SpkApplicationListClient(fast_config()) as app_client:
        result = check_and_notify(
            ipo_client=ipo_client,
            application_client=app_client,
            state_path=state_path,
            year=2024,
            send_existing=True,
            notifier=sent.append,
        )

    assert result.is_first_run is True
    assert len(result.notified_ipo_records) == 2
    assert len(result.notified_application_records) == 4
    assert len(sent) == 6
    assert any("Yeni halka arz:" in text for text in sent)
    assert any("Yeni halka arz başvurusu:" in text for text in sent)


def test_later_run_only_notifies_records_not_already_seen(httpx_mock, fixture_json, fixture_html, tmp_path):
    _mock_both_sources(httpx_mock, fixture_json, fixture_html)
    state_path = tmp_path / "seen.json"

    # Simulate a prior run that already saw PATEK and the earliest Multinet application.
    pre_state = SeenRecordsState(initialized_at_utc="2026-01-01T00:00:00Z")
    pre_state.ipo_identities.add("ipo:PATEK:2024 / 2")
    pre_state.application_identities.add("application:Multinet Kurumsal Hizmetler AŞ:2023-10-17")
    save_state(state_path, pre_state)

    sent: list[str] = []
    with SpkApiClient(fast_config()) as ipo_client, SpkApplicationListClient(fast_config()) as app_client:
        result = check_and_notify(
            ipo_client=ipo_client,
            application_client=app_client,
            state_path=state_path,
            year=2024,
            send_existing=False,
            notifier=sent.append,
        )

    assert result.is_first_run is False
    assert len(result.new_ipo_records) == 1
    assert result.new_ipo_records[0].borsa_kodu == "BORSK"
    assert len(result.new_application_records) == 3  # everything except the pre-seen Multinet row

    # Not a first run, so new == notified even without --send-existing.
    assert result.notified_ipo_records == result.new_ipo_records
    assert result.notified_application_records == result.new_application_records
    assert len(sent) == 4
    assert any("BORSK" in text for text in sent)
    assert not any("PATEK" in text for text in sent)

    saved = json.loads(state_path.read_text())
    assert len(saved["ipo_identities"]) == 2
    assert len(saved["application_identities"]) == 4


def test_end_to_end_with_real_send_message_and_mocked_telegram(httpx_mock, fixture_json, fixture_html, tmp_path):
    """Integration-style: SPK responses AND the Telegram send are both mocked,
    exercising the real send_message() function (not a fake notifier)."""
    _mock_both_sources(httpx_mock, fixture_json, fixture_html)
    telegram_url = "https://api.telegram.org/bot123:ABC/sendMessage"
    for _ in range(6):  # one response per expected notification (2 IPO + 4 applications)
        httpx_mock.add_response(url=telegram_url, json={"ok": True})

    state_path = tmp_path / "seen.json"
    creds = TelegramCredentials(bot_token="123:ABC", chat_id="999")

    with SpkApiClient(fast_config()) as ipo_client, SpkApplicationListClient(fast_config()) as app_client:
        result = check_and_notify(
            ipo_client=ipo_client,
            application_client=app_client,
            state_path=state_path,
            year=2024,
            send_existing=True,
            notifier=lambda text: send_message(creds, text, config=fast_config()),
        )

    assert len(result.notified_ipo_records) + len(result.notified_application_records) == 6
    telegram_requests = [r for r in httpx_mock.get_requests() if r.url.host == "api.telegram.org"]
    assert len(telegram_requests) == 6
