from __future__ import annotations

import contextlib
import hashlib
import os
import stat
from pathlib import Path

from telethon import TelegramClient

from deathtg.config import RUNTIME_DIR
from deathtg.telethon_policy import client_retry_kwargs


def _protect_session_storage(base_dir: Path, session_base: Path) -> None:
    try:
        os.chmod(base_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        pass
    for path in base_dir.glob(f"{session_base.name}.session*"):
        if not path.is_file():
            continue
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def _token_fingerprint(token: str) -> str:
    clean = (token or "").strip()
    bot_id = clean.split(":", 1)[0].strip()
    digest = hashlib.sha256(clean.encode("utf-8", errors="ignore")).hexdigest()[:10]
    if bot_id.isdigit():
        return f"{bot_id}_{digest}"
    return digest


def bot_session_base(role: str, token: str) -> Path:
    safe_role = "".join(ch for ch in str(role or "bot").lower() if ch.isalnum() or ch == "_") or "bot"
    base_dir = RUNTIME_DIR / "bot_sessions"
    base_dir.mkdir(parents=True, exist_ok=True)
    session_base = base_dir / f"{safe_role}_{_token_fingerprint(token)}"
    _protect_session_storage(base_dir, session_base)
    return session_base


def drop_session_files(session_base: Path) -> None:
    parent = session_base.parent
    stem = session_base.name
    for path in parent.glob(stem + ".session*"):
        with contextlib.suppress(OSError):
            path.unlink()


async def start_bot_client(
    *,
    role: str,
    token: str,
    api_id: int,
    api_hash: str,
) -> tuple[TelegramClient, object, Path]:
    session_base = bot_session_base(role, token)
    last_error: Exception | None = None
    for attempt in range(2):
        client = TelegramClient(str(session_base), api_id, api_hash, **client_retry_kwargs())
        try:
            await client.start(bot_token=token)
            me = await client.get_me()
            if not getattr(me, "bot", False):
                raise RuntimeError(f"{role} session is bound to a user, not a bot")
            _protect_session_storage(session_base.parent, session_base)
            return client, me, session_base
        except Exception as exc:
            last_error = exc
            with contextlib.suppress(Exception):
                await client.disconnect()
            if attempt == 0 and "not a bot" in str(exc).lower():
                drop_session_files(session_base)
                continue
            raise
    raise RuntimeError(str(last_error or "Bot session start failed"))
