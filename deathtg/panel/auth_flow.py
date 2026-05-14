from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from deathtg.config import ENV_PATH, ROOT_DIR


@dataclass
class PendingLogin:
    client: TelegramClient
    phone: str
    api_id: int
    api_hash: str
    session_name: str
    phone_code_hash: str | None = None


PENDING: dict[str, PendingLogin] = {}


def write_env(api_id: int, api_hash: str, session_name: str, phone: str, panel_key: str, panel_secret: str, bot_token: str = "") -> None:
    content = f"""API_ID={api_id}
API_HASH={api_hash}
SESSION_NAME={session_name}
COMMAND_PREFIX=.
OWNER_ID=
BOT_TOKEN={bot_token}
PANEL_PASSWORD={panel_key}
PANEL_SECRET={panel_secret}
PHONE={phone}
"""
    ENV_PATH.write_text(content, encoding="utf-8")


async def begin_login(flow_id: str, api_id: int, api_hash: str, phone: str, session_name: str) -> None:
    session_path = str(ROOT_DIR / session_name)
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    sent = await client.send_code_request(phone)
    PENDING[flow_id] = PendingLogin(
        client=client,
        phone=phone,
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        phone_code_hash=sent.phone_code_hash,
    )


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
