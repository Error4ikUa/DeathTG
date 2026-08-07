from __future__ import annotations

import contextlib
import html
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from telethon import TelegramClient, events

from deathtg.bot_sessions import start_bot_client
from deathtg.community_roles import (
    allowed_role,
    community_bot_display_name,
    community_enabled_for_owner,
    issue_role_invite,
    list_role_entries,
    preferred_community_bot_username,
    redeem_role_invite,
    revoke_role,
)
from deathtg.config import ENV_PATH
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
        try:
            client, me, _session_base = await start_bot_client(
                role="community",
                token=token,
                api_id=self.api_id,
                api_hash=self.api_hash,
            )
            self.bot_username = getattr(me, "username", "") or preferred_community_bot_username()
            self.bot_client = client
            self.error = None
            client.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(Exception):
                if self.bot_client:
                    await self.bot_client.disconnect()

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
        command = lowered.split(maxsplit=1)[0]

        if command == "/start":
            await event.reply(
                "DeathTG Community is online.\n\n"
                "Send the one-time key issued by the DeathTG owner to activate an Admin or Developer title."
            )
            return
        if command == "/scan":
            await self._handle_scan(event, lowered, sender_id)
            return
        if command == "/redeem" or re.fullmatch(r"dtg-(?:adm|dev)-[a-z0-9-]+", lowered, re.I):
            code = text.split(maxsplit=1)[1].strip() if command == "/redeem" and " " in text else text
            await self._handle_redeem(event, code)
            return

        if sender_id != OWNER_TG_ID:
            if command.startswith("/"):
                await event.reply("Send your one-time DeathTG role key here. If the owner service is offline, try again later.")
            return

        if command in {"/userinfo", "/list"}:
            await self._handle_list(event)
            return
        if command in {"/aduserdev", "/addev"}:
            await self._handle_issue(event, text, "developer")
            return
        if command in {"/aduseradm", "/adadmn", "/adadmin", "/adadm"}:
            await self._handle_issue(event, text, "admin")
            return
        if command in {"/deluserdev", "/deldev"}:
            await self._handle_revoke(event, text, "developer")
            return
        if command in {"/deluseradm", "/deladm"}:
            await self._handle_revoke(event, text, "admin")
            return

    async def _handle_scan(self, event: events.NewMessage.Event, lowered: str, sender_id: int) -> None:
        parts = lowered.split()
        if len(parts) < 3 or not parts[1].isdigit():
            await event.reply("false")
            return
        user_id = int(parts[1])
        role = parts[2]
        if sender_id not in {OWNER_TG_ID, user_id}:
            await event.reply("false")
            return
        await event.reply("true" if allowed_role(user_id, role) else "false")

    async def _handle_list(self, event: events.NewMessage.Event) -> None:
        entries = list_role_entries()
        if not entries:
            await event.reply(f"{community_bot_display_name()}: пока нет активных администраторов и разработчиков.")
            return
        rows = ["<b>DeathTG access registry</b>"]
        for item in entries[:100]:
            user_id = int(item["user_id"])
            username = str(item.get("username") or "").lstrip("@")
            display_name = str(item.get("display_name") or username or user_id)
            linked_name = f'<a href="tg://user?id={user_id}">{html.escape(display_name)}</a>'
            tag = f"@{html.escape(username)}" if username else "@not_set"
            title = ", ".join(html.escape(str(value)) for value in item.get("titles", []))
            rows.append(f"\n{linked_name}\n{tag} · <code>{user_id}</code>\n{title}")
        await event.reply("\n".join(rows), parse_mode="html", link_preview=False)

    async def _handle_issue(self, event: events.NewMessage.Event, text: str, role: str) -> None:
        target_user_id = self._extract_user_id(text)
        invite = issue_role_invite(
            role,
            actor_id=OWNER_TG_ID,
            target_user_id=target_user_id,
        )
        expires_in = max(1, (int(invite["expires_at"]) - int(time.time())) // 60)
        target_note = f"\nДля Telegram ID: <code>{target_user_id}</code>" if target_user_id else "\nКлюч активирует первый пользователь, который его введёт."
        await event.reply(
            "<b>Одноразовый ключ DeathTG</b>\n\n"
            f"Титул: <b>{html.escape(str(invite['title']))}</b>\n"
            f"Ключ: <code>{invite['code']}</code>\n"
            f"Действует: {expires_in} мин.{target_note}\n\n"
            "Передайте ключ пользователю. После активации повторно использовать его нельзя.",
            parse_mode="html",
        )

    async def _handle_redeem(self, event: events.NewMessage.Event, code: str) -> None:
        sender = await event.get_sender()
        sender_id = int(getattr(sender, "id", 0) or 0)
        username = str(getattr(sender, "username", "") or "")
        display_name = " ".join(
            part for part in (getattr(sender, "first_name", "") or "", getattr(sender, "last_name", "") or "") if part
        ).strip()
        result = redeem_role_invite(
            code,
            user_id=sender_id,
            username=username,
            display_name=display_name,
        )
        if not result.get("ok"):
            await event.reply(str(result.get("message") or "Не удалось активировать ключ."))
            return
        await event.reply(
            "<b>Связка с DeathTG успешно создана.</b>\n\n"
            f"Поздравляем, {html.escape(display_name or username or str(sender_id))}.\n"
            f"Ваш титул: <b>{html.escape(str(result['title']))}</b>.\n\n"
            "Теперь выберите этот титул в настройках профиля DeathTG.",
            parse_mode="html",
        )

    async def _handle_revoke(self, event: events.NewMessage.Event, text: str, role: str) -> None:
        user_id = self._extract_user_id(text)
        if not user_id:
            await event.reply(f"Usage: /{'deluserdev' if role == 'developer' else 'deluseradm'} <telegram_id>")
            return
        revoke_role(user_id, role, actor_id=OWNER_TG_ID)
        await event.reply(f"Доступ отозван: {user_id} -> {role}")

    @staticmethod
    def _extract_user_id(text: str) -> int | None:
        match = re.search(r"(\d{5,})", text)
        return int(match.group(1)) if match else None
