from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
import threading
import time
from pathlib import Path

from deathtg.config import RUNTIME_DIR
from deathtg.panel_access import panel_base_url


SETUP_TOKEN_PATH = RUNTIME_DIR / "setup_token.txt"
SETUP_TOKEN_LOCK = threading.RLock()
DEFAULT_SETUP_TOKEN_TTL = 6 * 60 * 60


def _token_ttl() -> int:
    try:
        value = int(os.getenv("PANEL_SETUP_TOKEN_TTL", str(DEFAULT_SETUP_TOKEN_TTL)))
    except ValueError:
        value = DEFAULT_SETUP_TOKEN_TTL
    return max(300, min(value, 24 * 60 * 60))


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _read_token_record() -> tuple[str, int]:
    if not SETUP_TOKEN_PATH.exists():
        return "", 0
    try:
        raw = SETUP_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return "", 0
    if not raw:
        return "", 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Migrate the original plain-text format without breaking an active setup.
        try:
            created_at = int(SETUP_TOKEN_PATH.stat().st_mtime)
        except OSError:
            created_at = int(time.time())
        return raw, created_at
    if not isinstance(payload, dict):
        return "", 0
    return str(payload.get("token") or ""), int(payload.get("created_at") or 0)


def _write_token_record(token: str, created_at: int) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    temporary = SETUP_TOKEN_PATH.with_name(
        f".{SETUP_TOKEN_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps({"token": token, "created_at": int(created_at)}, separators=(",", ":")),
            encoding="utf-8",
        )
        _chmod_private(temporary)
        os.replace(temporary, SETUP_TOKEN_PATH)
        _chmod_private(SETUP_TOKEN_PATH)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _rotate_locked() -> str:
    token = secrets.token_urlsafe(32)
    _write_token_record(token, int(time.time()))
    return token


def ensure_setup_token() -> str:
    with SETUP_TOKEN_LOCK:
        token, created_at = _read_token_record()
        if token and created_at and time.time() - created_at <= _token_ttl():
            _chmod_private(SETUP_TOKEN_PATH)
            return token
        return _rotate_locked()


def rotate_setup_token() -> str:
    with SETUP_TOKEN_LOCK:
        return _rotate_locked()


def current_setup_token() -> str:
    return ensure_setup_token()


def setup_link() -> str:
    return f"{panel_base_url()}/setup?setup_token={ensure_setup_token()}"


def valid_setup_token(value: str) -> bool:
    candidate = (value or "").strip()
    if not candidate:
        return False
    with SETUP_TOKEN_LOCK:
        token, created_at = _read_token_record()
        if not token or not created_at or time.time() - created_at > _token_ttl():
            return False
        return hmac.compare_digest(candidate, token)


def consume_setup_token(value: str) -> bool:
    """Atomically consume a remote setup URL and rotate its secret."""
    candidate = (value or "").strip()
    if not candidate:
        return False
    with SETUP_TOKEN_LOCK:
        token, created_at = _read_token_record()
        if not token or not created_at or time.time() - created_at > _token_ttl():
            return False
        if not hmac.compare_digest(candidate, token):
            return False
        _rotate_locked()
        return True
