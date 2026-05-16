from __future__ import annotations

import contextlib
import inspect
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from telethon import Button, TelegramClient, events
from telethon.errors import UserDeactivatedError

from deathtg.config import ENV_PATH, RUNTIME_DIR


CallbackFunc = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class CallbackEntry:
    form_id: str
    button_id: str
    func: CallbackFunc
    args: tuple
    created_at: float
    ttl: int
    public: bool = False
    allow_users: tuple[int, ...] = ()


@dataclass(slots=True)
class FormEntry:
    form_id: str
    text: str
    buttons: Any
    parse_mode: str | None
    link_preview: bool | None
    original_chat_id: int | None
    original_client: Any
    original_message_id: int | None
    initiator_user_id: int | None
    created_at: float
    ttl: int


class InlineCall:
    def __init__(self, manager: "InlineManager", event, form: FormEntry | None = None) -> None:
        self._manager = manager
        self._event = event
        self._form = form
        self.data = getattr(event, "data", b"")
        self.chat_id = getattr(event, "chat_id", None)
        self.sender_id = getattr(event, "sender_id", None)
        self.user_id = self.sender_id
        self.original_chat_id = form.original_chat_id if form else self.chat_id
        self.original_client = form.original_client if form else manager.user_client
        self.original_message_id = form.original_message_id if form else None
        self.form_id = form.form_id if form else None

    async def answer(self, text: str | None = None, **kwargs):
        return await self._event.answer(text or "", **kwargs)

    async def edit(self, text: str, reply_markup=None, **kwargs):
        ttl = int(kwargs.pop("ttl", 3600) or 3600)
        buttons = self._manager.markup(
            reply_markup,
            ttl=ttl,
            form_id=(self._form.form_id if self._form else None),
        )
        return await self._event.edit(text, buttons=buttons, **kwargs)

    async def delete(self):
        return await self._event.delete()


class InlineManager:
    def __init__(self, *, api_id: int, api_hash: str, user_client=None) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.user_client = user_client
        self.bot_client: TelegramClient | None = None
        self.bot_username = ""
        self.owner_id: int | None = None
        self.owner_username = ""
        self.error: str | None = "Inline bot is not configured"
        self.last_error: str = ""
        self.registry: dict[bytes, CallbackEntry] = {}
        self.forms: dict[str, FormEntry] = {}

    @property
    def ready(self) -> bool:
        return bool(self.bot_client and self.bot_client.is_connected() and not self.error)

    def status(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "username": self.bot_username,
            "error": self.error,
            "last_error": self.last_error,
            "callbacks": len(self.registry),
            "forms": len(self.forms),
            "owner_id": self.owner_id,
            "owner_username": self.owner_username,
        }

    async def start(self) -> None:
        load_dotenv(ENV_PATH, override=True)
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            self.error = "Inline bot is not configured"
            return
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        session = str(RUNTIME_DIR / "inline_bot")
        client = TelegramClient(session, self.api_id, self.api_hash)
        try:
            await client.start(bot_token=token)
            me = await client.get_me()
            if not getattr(me, "bot", False):
                raise RuntimeError("Inline session is bound to a user, not bot. Recreating session...")
            self.bot_username = getattr(me, "username", "") or ""
            self.bot_client = client
            self.error = None
            await self._read_owner()
            client.add_event_handler(self._on_callback, events.CallbackQuery)
            client.add_event_handler(self._on_inline_query, events.InlineQuery)
            client.add_event_handler(self._on_start, events.NewMessage(incoming=True, pattern=r"(?i)^/start(?:\s|$)"))
            client.add_event_handler(self._on_status, events.NewMessage(incoming=True, pattern=r"(?i)^/status(?:\s|$)"))
            client.add_event_handler(self._on_private_message, events.NewMessage(incoming=True, func=self._is_private_message))
        except Exception as exc:
            if isinstance(exc, UserDeactivatedError) or exc.__class__.__name__ == "UserDeactivatedError" or "Recreating session" in str(exc):
                await self._drop_session_files()
                client = TelegramClient(session, self.api_id, self.api_hash)
                try:
                    await client.start(bot_token=token)
                    me = await client.get_me()
                    if not getattr(me, "bot", False):
                        raise RuntimeError("Inline session still not authorized as bot")
                    self.bot_username = getattr(me, "username", "") or ""
                    self.bot_client = client
                    self.error = None
                    await self._read_owner()
                    client.add_event_handler(self._on_callback, events.CallbackQuery)
                    client.add_event_handler(self._on_inline_query, events.InlineQuery)
                    client.add_event_handler(self._on_start, events.NewMessage(incoming=True, pattern=r"(?i)^/start(?:\s|$)"))
                    client.add_event_handler(self._on_status, events.NewMessage(incoming=True, pattern=r"(?i)^/status(?:\s|$)"))
                    client.add_event_handler(self._on_private_message, events.NewMessage(incoming=True, func=self._is_private_message))
                    return
                except Exception as retry_exc:
                    exc = retry_exc
            self.error = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def _read_owner(self) -> None:
        if not self.user_client:
            return
        try:
            me = await self.user_client.get_me()
            self.owner_id = int(getattr(me, "id", 0) or 0) or None
            self.owner_username = getattr(me, "username", "") or ""
        except Exception:
            self.owner_id = None
            self.owner_username = ""

    async def _drop_session_files(self) -> None:
        for path in RUNTIME_DIR.glob("inline_bot.session*"):
            with contextlib.suppress(OSError):
                path.unlink()

    async def stop(self) -> None:
        if self.bot_client:
            await self.bot_client.disconnect()

    def _cleanup(self) -> None:
        now = time.time()
        for token, entry in list(self.registry.items()):
            if now - entry.created_at > entry.ttl:
                self.registry.pop(token, None)
        for form_id, form in list(self.forms.items()):
            if now - form.created_at > form.ttl:
                self.forms.pop(form_id, None)

    @staticmethod
    def _next_form_id() -> str:
        return "f" + secrets.token_hex(8)

    @staticmethod
    def _next_button_id() -> str:
        return "b" + secrets.token_hex(6)

    def _callback_button(
        self,
        text: str,
        callback: CallbackFunc,
        args: tuple,
        ttl: int,
        form_id: str,
        *,
        public: bool = False,
        allow_users: tuple[int, ...] = (),
    ) -> Button:
        self._cleanup()
        button_id = self._next_button_id()
        token = f"dtg:{form_id}:{button_id}".encode("utf-8")[:64]
        self.registry[token] = CallbackEntry(
            form_id=form_id,
            button_id=button_id,
            func=callback,
            args=tuple(args or ()),
            created_at=time.time(),
            ttl=ttl,
            public=public,
            allow_users=tuple(int(user) for user in allow_users if user),
        )
        return Button.inline(text, token)

    def button(self, item: dict, *, ttl: int = 3600, form_id: str | None = None) -> Button | None:
        text = str(item.get("text") or "Button")
        url = item.get("url")
        if url:
            return Button.url(text, str(url))
        callback = item.get("callback")
        if callback:
            if not form_id:
                return None
            args = item.get("args") or ()
            if not isinstance(args, tuple):
                args = tuple(args if isinstance(args, list) else (args,))
            allow_users = item.get("allow_users") or item.get("always_allow") or ()
            if not isinstance(allow_users, tuple):
                allow_users = tuple(allow_users if isinstance(allow_users, list) else (allow_users,))
            return self._callback_button(
                text,
                callback,
                args,
                ttl,
                form_id=form_id,
                public=bool(item.get("public") or item.get("disable_security")),
                allow_users=allow_users,
            )
        return None

    def markup(self, reply_markup, *, ttl: int = 3600, form_id: str | None = None):
        if not reply_markup:
            return None
        rows = []
        for row in reply_markup:
            source = row if isinstance(row, (list, tuple)) else [row]
            buttons = []
            for item in source:
                if isinstance(item, dict):
                    button = self.button(item, ttl=ttl, form_id=form_id)
                    if button is not None:
                        buttons.append(button)
                else:
                    buttons.append(item)
            if buttons:
                rows.append(buttons)
        return rows or None

    async def _send_missing(self, message=None):
        text = "Inline bot is not configured"
        if message is not None and hasattr(message, "edit"):
            return await message.edit(text)
        return None

    async def _fallback_edit(self, message, text: str, **kwargs):
        if message is None or not hasattr(message, "edit"):
            return None
        try:
            return await message.edit(
                text,
                parse_mode=kwargs.get("parse_mode"),
                link_preview=kwargs.get("link_preview"),
            )
        except TypeError:
            return await message.edit(text, parse_mode=kwargs.get("parse_mode"))

    async def form(self, text: str, *, message=None, reply_markup=None, ttl: int = 3600, **kwargs):
        if not self.ready:
            return await self._fallback_edit(message, text, **kwargs)
        form_id = self._next_form_id()
        chat = kwargs.get("chat") or kwargs.get("chat_id") or getattr(message, "chat_id", None)
        if chat is None:
            return await self._fallback_edit(message, text, **kwargs)
        original_client = getattr(message, "client", None) or self.user_client
        original_message_id = getattr(message, "id", None)
        initiator_user_id = getattr(message, "sender_id", None) or self.owner_id
        buttons = self.markup(reply_markup, ttl=ttl, form_id=form_id)
        self.forms[form_id] = FormEntry(
            form_id=form_id,
            text=text,
            buttons=buttons,
            parse_mode=kwargs.get("parse_mode"),
            link_preview=kwargs.get("link_preview"),
            original_chat_id=chat,
            original_client=original_client,
            original_message_id=original_message_id,
            initiator_user_id=initiator_user_id,
            created_at=time.time(),
            ttl=int(ttl or 3600),
        )
        try:
            sent = await self._insert_inline_form(self.forms[form_id], chat, message=message, **kwargs)
        except Exception as exc:
            self.forms.pop(form_id, None)
            return await self._fallback_edit(message, text, **kwargs)
        if message is not None:
            try:
                await message.delete()
            except Exception:
                try:
                    await message.edit("Inline form opened in bot message.")
                except Exception:
                    pass
        return sent

    async def _insert_inline_form(self, form: FormEntry, chat, *, message=None, **kwargs):
        if not self.user_client or not self.bot_username:
            self.last_error = "User client or bot username is missing"
            return await self._send_missing(message)
        query = f"dtg:{form.form_id}"
        bot = self.bot_username if self.bot_username.startswith("@") else f"@{self.bot_username}"
        try:
            results = await self.user_client.inline_query(bot, query, entity=chat)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        if not results:
            self.last_error = "Inline query returned no results. Enable inline mode in BotFather with /setinline."
            raise RuntimeError("Inline result was not returned")
        reply_to = kwargs.get("reply_to")
        if reply_to is None:
            reply_to = getattr(message, "reply_to_msg_id", None)
        try:
            sent = await results[0].click(chat, reply_to=reply_to)
            self.last_error = ""
            return sent
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise

    async def send_or_edit(
        self,
        event,
        text: str,
        *,
        buttons=None,
        reply_markup=None,
        parse_mode: str | None = "html",
        link_preview: bool | None = None,
        ttl: int = 3600,
        **kwargs,
    ):
        """Back-compat bridge for external DTG modules expecting old inline API."""
        markup = reply_markup if reply_markup is not None else buttons
        if not self.ready:
            if event is not None and hasattr(event, "edit"):
                try:
                    return await event.edit(text, buttons=markup, parse_mode=parse_mode, link_preview=link_preview)
                except Exception:
                    return await self._send_missing(event)
            return await self._send_missing(event)
        return await self.form(
            text,
            message=event,
            reply_markup=markup,
            ttl=ttl,
            parse_mode=parse_mode,
            link_preview=link_preview,
            **kwargs,
        )

    async def _on_callback(self, event) -> None:
        self._cleanup()
        entry = self.registry.get(getattr(event, "data", b""))
        if not entry:
            await event.answer("This button expired.", alert=True)
            return
        form = self.forms.get(entry.form_id)
        if not self._can_press(event, form, entry):
            await event.answer("This button is private.", alert=True)
            return
        call = InlineCall(self, event, form=form)
        try:
            params = inspect.signature(entry.func).parameters
            if len(params) <= 1:
                await entry.func(call)
            else:
                await entry.func(call, *entry.args)
        except TypeError:
            await entry.func(call, *entry.args)
        except Exception as exc:
            await event.answer(f"{type(exc).__name__}: {exc}", alert=True)

    def _can_press(self, event, form: FormEntry | None, entry: CallbackEntry) -> bool:
        if entry.public:
            return True
        user_id = getattr(event, "sender_id", None)
        if user_id is None:
            return False
        allowed = set(entry.allow_users)
        if self.owner_id:
            allowed.add(int(self.owner_id))
        if form and form.initiator_user_id:
            allowed.add(int(form.initiator_user_id))
        return int(user_id) in allowed

    async def _on_inline_query(self, event) -> None:
        self._cleanup()
        query = (getattr(event, "text", "") or "").strip()
        if not query.startswith("dtg:"):
            await event.answer([], cache_time=0, private=True)
            return
        form_id = query.split(":", 1)[1]
        form = self.forms.get(form_id)
        if not form:
            result = event.builder.article(
                "DeathTG",
                description="Inline form expired",
                text="This inline form expired.",
                parse_mode="html",
            )
            await event.answer([result], cache_time=0, private=True)
            return
        result = event.builder.article(
            "DeathTG",
            description="Inline form",
            text=form.text,
            buttons=form.buttons,
            parse_mode=form.parse_mode or "html",
            link_preview=bool(form.link_preview),
        )
        await event.answer([result], cache_time=0, private=True)

    def _owner_line(self) -> str:
        if self.owner_id and self.owner_username:
            return f"Owner: {self.owner_id} (@{self.owner_username})"
        if self.owner_id:
            return f"Owner: {self.owner_id}"
        return "Owner: unknown"

    @staticmethod
    def _is_private_message(event) -> bool:
        if not getattr(event, "is_private", False):
            return False
        text = (getattr(event, "raw_text", "") or "").strip().lower()
        return text not in {"/start", "/status"}

    @staticmethod
    def _help_buttons():
        return [
            [Button.url("News", "https://t.me/Death_Telega"), Button.url("Support", "https://t.me/Death_TgOfftop")],
        ]

    async def _on_start(self, event) -> None:
        text = (
            "DeathTG inline bot is connected.\n"
            f"{self._owner_line()}\n"
            f"Bot: @{self.bot_username or 'unknown'}\n\n"
            "Commands:\n"
            "/status - runtime status\n"
            "/start - this message"
        )
        await event.respond(text, buttons=self._help_buttons())

    async def _on_status(self, event) -> None:
        ready = "yes" if self.ready else "no"
        text = (
            "DeathTG inline status:\n"
            f"ready: {ready}\n"
            f"callbacks: {len(self.registry)}\n"
            f"{self._owner_line()}"
        )
        await event.respond(text, buttons=self._help_buttons())

    async def _on_private_message(self, event) -> None:
        text = (
            "Buttons and inline forms are managed by the DeathTG userbot.\n"
            "Use /status to check readiness."
        )
        await event.respond(text, buttons=self._help_buttons())
