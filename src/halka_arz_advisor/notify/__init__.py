"""Notification MVP: poll SPK, diff against persisted state, notify via Telegram.

Deliberately minimal — no scoring, recommendations, KAP, database, ML,
UI, or scheduling here. See ``scripts/check_and_notify.py`` for the CLI.
"""

from .check import CheckResult, check_and_notify
from .identity import application_identity, ipo_identity
from .state import SeenRecordsState, load_state, save_state
from .telegram import (
    TelegramConfigError,
    TelegramCredentials,
    TelegramSendError,
    load_credentials_from_env,
    send_message,
)

__all__ = [
    "CheckResult",
    "check_and_notify",
    "ipo_identity",
    "application_identity",
    "SeenRecordsState",
    "load_state",
    "save_state",
    "TelegramCredentials",
    "TelegramConfigError",
    "TelegramSendError",
    "load_credentials_from_env",
    "send_message",
]
