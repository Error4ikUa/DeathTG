from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from deathtg.config import ROOT_DIR
from deathtg.server_bootstrap import secure_panel_password, secure_panel_secret, update_env_values


@dataclass
class PendingLogin:
    client: TelegramClient
    phone: str
    api_id: int
    api_hash: str
    session_name: str
    phone_code_hash: str | None = None
    delivery_hint: str = ""
    timeout_seconds: int | None = None


PENDING: dict[str, PendingLogin] = {}


def _set_login_pending(value: bool) -> None:
    update_env_values({"LOGIN_PENDING": "1" if value else "0"})


def _cleanup_session_files(session_name: str) -> None:
    for path in ROOT_DIR.glob(f"{session_name}.session*"):
        try:
            path.unlink()
        except Exception:
            pass


def _delivery_hint(sent) -> tuple[str, int | None]:
    code_type = getattr(sent, "type", None)
    timeout = getattr(sent, "timeout", None)
    type_name = code_type.__class__.__name__ if code_type is not None else ""
    mapping = {
        "SentCodeTypeApp": "Telegram sent the login code inside the Telegram app.",
        "SentCodeTypeSms": "Telegram sent the login code by SMS.",
        "SentCodeTypeCall": "Telegram will deliver the login code by phone call.",
        "SentCodeTypeFlashCall": "Telegram will deliver the login code by flash call.",
        "SentCodeTypeMissedCall": "Telegram will deliver the login code by missed call.",
        "SentCodeTypeEmailCode": "Telegram sent the login code to your email.",
    }
    hint = mapping.get(type_name, "Telegram requested a login code for this account.")
    return hint, int(timeout) if isinstance(timeout, int) else None


def write_env(api_id: int, api_hash: str, session_name: str, phone: str, panel_key: str, panel_secret: str, bot_token: str = "") -> None:
    update_env_values(
        {
            "API_ID": str(api_id),
            "API_HASH": api_hash.strip(),
            "SESSION_NAME": session_name.strip() or "deathtg",
            "COMMAND_PREFIX": ".",
            "BOT_TOKEN": bot_token.strip(),
            "PANEL_PASSWORD": secure_panel_password(panel_key),
            "PANEL_SECRET": secure_panel_secret(panel_secret),
            "PHONE": phone.strip(),
            "LOGIN_PENDING": "1",
        }
    )


async def begin_login(flow_id: str, api_id: int, api_hash: str, phone: str, session_name: str) -> str:
    _set_login_pending(True)
    _cleanup_session_files(session_name)
    session_path = str(ROOT_DIR / session_name)
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    if await client.is_user_authorized():
        PENDING[flow_id] = PendingLogin(
            client=client,
            phone=phone,
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_name,
            phone_code_hash=None,
        )
        return "authorized"
    sent = await client.send_code_request(phone)
    delivery_hint, timeout_seconds = _delivery_hint(sent)
    PENDING[flow_id] = PendingLogin(
        client=client,
        phone=phone,
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        phone_code_hash=sent.phone_code_hash,
        delivery_hint=delivery_hint,
        timeout_seconds=timeout_seconds,
    )
    return "code"


def login_hint(flow_id: str) -> dict[str, object]:
    pending = PENDING.get(flow_id)
    if not pending:
        return {"delivery_hint": "", "timeout_seconds": None}
    return {
        "delivery_hint": pending.delivery_hint,
        "timeout_seconds": pending.timeout_seconds,
    }


async def resend_code(flow_id: str) -> dict[str, object]:
    pending = PENDING[flow_id]
    sent = await pending.client.send_code_request(pending.phone)
    pending.phone_code_hash = sent.phone_code_hash
    pending.delivery_hint, pending.timeout_seconds = _delivery_hint(sent)
    return login_hint(flow_id)


async def confirm_code(flow_id: str, code: str) -> str:
    pending = PENDING[flow_id]
    try:
        await pending.client.sign_in(
            phone=pending.phone,
            code=code,
            phone_code_hash=pending.phone_code_hash,
        )
        return "done"
    except SessionPasswordNeededError:
        return "2fa"


async def confirm_2fa(flow_id: str, password: str) -> None:
    pending = PENDING[flow_id]
    await pending.client.sign_in(password=password)


async def finish_login(flow_id: str) -> dict[str, str]:
    pending = PENDING.pop(flow_id)
    me = await pending.client.get_me()
    await pending.client.disconnect()
    _set_login_pending(False)
    for path in ROOT_DIR.glob(f"{pending.session_name}.session*"):
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    return {
        "id": str(me.id),
        "first_name": me.first_name or "",
        "last_name": me.last_name or "",
        "username": me.username or "",
    }


async def cancel_login(flow_id: str) -> None:
    pending = PENDING.pop(flow_id, None)
    if pending:
        await pending.client.disconnect()
    if not PENDING:
        _set_login_pending(False)
