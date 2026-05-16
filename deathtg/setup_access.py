from __future__ import annotations

import secrets

from deathtg.config import RUNTIME_DIR
from deathtg.panel_access import panel_base_url


SETUP_TOKEN_PATH = RUNTIME_DIR / "setup_token.txt"


def ensure_setup_token() -> str:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if SETUP_TOKEN_PATH.exists():
        token = SETUP_TOKEN_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    SETUP_TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


def current_setup_token() -> str:
    return ensure_setup_token()


def setup_link() -> str:
    return f"{panel_base_url()}/setup?setup_token={ensure_setup_token()}"


def valid_setup_token(value: str) -> bool:
    return bool(value) and value.strip() == ensure_setup_token()
