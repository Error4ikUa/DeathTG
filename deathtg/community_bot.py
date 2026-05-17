from __future__ import annotations

import contextlib
import os
import re
from typing import Any

from dotenv import load_dotenv
from telethon import TelegramClient, events

from deathtg.community_roles import (
    allowed_role,
    community_bot_display_name,
    community_enabled_for_owner,
    grant_role,
    list_role_entries,
    preferred_community_bot_username,
    revoke_role,
)
from deathtg.config import ENV_PATH, RUNTIME_DIR
from deathtg.role_gate import OWNER_TG_ID


class CommunityBotService:
    def __init__(self, *, api_id: int, api_hash: str, user_client=None) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.user_client = user_client
        self.bot_client: TelegramClient | None = None
        self.bot_username = ""
        self.error: str | None = "Community bot is not configured"

    async def start(self, owner_id: int | None) -> None:
        if not community_enabled_for_owner(owner_id):
            self.error = "Community bot is owner-only"
            return
        load_dotenv(ENV_PATH, override=True)
        token = (os.getenv("BOT_TOKEN_COMMUNITY", "") or "").strip()
        if not token:
            self.error = "Community bot token is not configured"
            return
        session = str(RUNTIME_DIR / "community_bot")
        client = TelegramClient(session, self.api_id, self.api_hash)
        try:
            await client.start(bot_token=token)
            me = await client.get_me()
            self.bot_username = getattr(me, "username", "") or preferred_community_bot_username()
            self.bot_client = client
            self.error = None
            client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def stop(self) -> None:
        if self.bot_client:
            await self.bot_client.disconnect()
        self.bot_client = None

    def status(self) -> dict[str, Any]:
        return {
            "ready": bool(self.bot_client and self.bot_client.is_connected() and not self.error),
            "username": self.bot_username,
            "error": self.error,
        }

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            return
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        lowered = text.lower()

        if lowered.startswith("/scan"):
            await self._handle_scan(event, lowered)
            return

        if sender_id != OWNER_TG_ID:
            return

        if lowered.startswith("/list"):
            await self._handle_list(event)
            return
        if lowered.startswith(("/addev", "/adadmn", "/adadmin", "/adadm")):
            role = "developer" if lowered.startswith("/addev") else "admin"
            await self._handle_grant(event, text, role)
            return
        if lowered.startswith("/deldev"):
            await self._handle_revoke(event, text, "developer")
            return
        if lowered.startswith("/deladm"):
            await self._handle_revoke(event, text, "admin")
            return

    async def _handle_scan(self, event: events.NewMessage.Event, lowered: str) -> None:
        parts = lowered.split()
        if len(parts) < 3 or not parts[1].isdigit():
            await event.reply("false")
            return
        user_id = int(parts[1])
        role = parts[2]
        await event.reply("true" if allowed_role(user_id, role) else "false")

    async def _handle_list(self, event: events.NewMessage.Event) -> None:
        entries = list_role_entries()
        if not entries:
            await event.reply(f"{community_bot_display_name()}: no roles granted yet.")
            return
        rows = [f"{int(item['user_id'])} - {'/'.join(str(role).title() for role in item['roles'])}" for item in entries]
        await event.reply("\n".join(rows[:100]))

    async def _handle_grant(self, event: events.NewMessage.Event, text: str, role: str) -> None:
        user_id = self._extract_user_id(text)
        if not user_id:
            await event.reply(f"Usage: /{'addev' if role == 'developer' else 'adadmn'} <telegram_id>")
            return
        actor = await event.get_sender()
        grant_role(
            user_id,
            role,
            actor_id=int(getattr(actor, "id", 0) or 0),
            actor_name=getattr(actor, "username", "") or getattr(actor, "first_name", "") or "",
        )
        await event.reply(f"ok {user_id} -> {role}")

    async def _handle_revoke(self, event: events.NewMessage.Event, text: str, role: str) -> None:
        user_id = self._extract_user_id(text)
        if not user_id:
            await event.reply(f"Usage: /{'deldev' if role == 'developer' else 'deladm'} <telegram_id>")
            return
        revoke_role(user_id, role)
        await event.reply(f"removed {user_id} -> {role}")

    @staticmethod
    def _extract_user_id(text: str) -> int | None:
        match = re.search(r"(\d{5,})", text)
        return int(match.group(1)) if match else None
