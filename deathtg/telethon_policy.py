from __future__ import annotations

import logging
import os


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def client_retry_kwargs() -> dict[str, int]:
    return {
        "connection_retries": _env_int("TELEGRAM_CONNECTION_RETRIES", 2),
        "request_retries": _env_int("TELEGRAM_REQUEST_RETRIES", 2),
        "retry_delay": _env_int("TELEGRAM_RETRY_DELAY", 2),
    }


def quiet_telethon_network_logs() -> None:
    # Telethon can print one warning per retry per connection. Keep real errors,
    # but do not flood the launcher with repeated Windows network warnings.
    for name in (
        "telethon.network",
        "telethon.network.connection",
        "telethon.network.mtprotosender",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)

