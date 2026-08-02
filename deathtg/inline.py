from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv
from telethon import Button, TelegramClient, events
from telethon.errors import PeerIdInvalidError, UserDeactivatedError

from deathtg.assets import system_image
from deathtg.backup_manager import create_modules_backup
from deathtg.bot_sessions import drop_session_files, start_bot_client
from deathtg.config import ENV_PATH, RUNTIME_DIR
from deathtg.i18n import translate
from deathtg.panel_access import issue_device_grant, panel_remote_access_ready
from deathtg.premium_emoji import emoji_line, premium_emoji
from deathtg.profile_store import profile_settings, save_profile_settings


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
        self.owner_premium: bool = False
        self.error: str | None = "Inline bot is not configured"
        self.last_error: str = ""
        self._session_base = None
        self.registry: dict[bytes, CallbackEntry] = {}
        self.forms: dict[str, FormEntry] = {}
        self._owner_start_notice_window = 6 * 60 * 60

    @property
    def ready(self) -> bool:
        return bool(self.bot_client and self.bot_client.is_connected() and not self.error)

    def _is_owner_chat(self, chat_id: Any) -> bool:
        if self.owner_id is None:
            return False
        try:
            return int(chat_id) == int(self.owner_id)
        except (TypeError, ValueError):
            return False

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
        try:
            client, me, session_base = await start_bot_client(
                role="inline",
                token=token,
                api_id=self.api_id,
                api_hash=self.api_hash,
            )
            self._session_base = session_base
            self.bot_username = getattr(me, "username", "") or ""
            self.bot_client = client
            self.error = None
            await self._read_owner()
            client.add_event_handler(self._on_callback, events.CallbackQuery)
            client.add_event_handler(self._on_inline_query, events.InlineQuery)
            client.add_event_handler(self._on_start, events.NewMessage(incoming=True, pattern=r"(?i)^/start(?:\s|$)"))
            client.add_event_handler(self._on_status, events.NewMessage(incoming=True, pattern=r"(?i)^/status(?:\s|$)"))
            client.add_event_handler(self._on_lang, events.NewMessage(incoming=True, pattern=r"(?i)^/lang(?:\s|$)"))
            client.add_event_handler(self._on_private_message, events.NewMessage(incoming=True, func=self._is_private_message))
        except Exception as exc:
            if isinstance(exc, UserDeactivatedError) or exc.__class__.__name__ == "UserDeactivatedError":
                await self._drop_session_files()
                try:
                    client, me, session_base = await start_bot_client(
                        role="inline",
                        token=token,
                        api_id=self.api_id,
                        api_hash=self.api_hash,
                    )
                    self._session_base = session_base
                    self.bot_username = getattr(me, "username", "") or ""
                    self.bot_client = client
                    self.error = None
                    await self._read_owner()
                    client.add_event_handler(self._on_callback, events.CallbackQuery)
                    client.add_event_handler(self._on_inline_query, events.InlineQuery)
                    client.add_event_handler(self._on_start, events.NewMessage(incoming=True, pattern=r"(?i)^/start(?:\s|$)"))
                    client.add_event_handler(self._on_status, events.NewMessage(incoming=True, pattern=r"(?i)^/status(?:\s|$)"))
                    client.add_event_handler(self._on_lang, events.NewMessage(incoming=True, pattern=r"(?i)^/lang(?:\s|$)"))
                    client.add_event_handler(self._on_private_message, events.NewMessage(incoming=True, func=self._is_private_message))
                    return
                except Exception as retry_exc:
                    exc = retry_exc
            self.error = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(Exception):
                if self.bot_client:
                    await self.bot_client.disconnect()

    async def _read_owner(self) -> None:
        if not self.user_client:
            return
        try:
            me = await self.user_client.get_me()
            self.owner_id = int(getattr(me, "id", 0) or 0) or None
            self.owner_username = getattr(me, "username", "") or ""
            self.owner_premium = bool(getattr(me, "premium", False))
        except Exception:
            self.owner_id = None
            self.owner_username = ""
            self.owner_premium = False

    async def _drop_session_files(self) -> None:
        if self._session_base:
            drop_session_files(self._session_base)
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

    async def push_form(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup=None,
        ttl: int = 3600,
        parse_mode: str | None = "html",
        link_preview: bool | None = False,
        photo: str | None = None,
    ):
        if not self.ready or not self.bot_client:
            return None
        form_id = self._next_form_id()
        buttons = self.markup(reply_markup, ttl=ttl, form_id=form_id)
        self.forms[form_id] = FormEntry(
            form_id=form_id,
            text=text,
            buttons=buttons,
            parse_mode=parse_mode,
            link_preview=link_preview,
            original_chat_id=chat_id,
            original_client=self.user_client,
            original_message_id=None,
            initiator_user_id=chat_id,
            created_at=time.time(),
            ttl=int(ttl or 3600),
        )
        try:
            target = chat_id
            try:
                target = await self.bot_client.get_input_entity(chat_id)
            except Exception:
                if self._is_owner_chat(chat_id):
                    self.last_error = "Owner has not started the inline bot yet"
                    self.forms.pop(form_id, None)
                    await self._notify_owner_start_inline_bot()
                    return None
                raise
            if photo:
                sent = await self.bot_client.send_file(
                    target,
                    file=photo,
                    caption=text,
                    buttons=buttons,
                    parse_mode=parse_mode,
                )
            else:
                sent = await self.bot_client.send_message(
                    target,
                    text,
                    buttons=buttons,
                    parse_mode=parse_mode,
                    link_preview=link_preview,
                )
        except PeerIdInvalidError:
            self.last_error = "Owner has not started the inline bot yet"
            self.forms.pop(form_id, None)
            if self._is_owner_chat(chat_id):
                await self._notify_owner_start_inline_bot()
                return None
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.forms.pop(form_id, None)
            if self._is_owner_chat(chat_id) and "Peer" in type(exc).__name__:
                await self._notify_owner_start_inline_bot()
                return None
            raise
        return sent

    async def _notify_owner_start_inline_bot(self) -> None:
        if not self.user_client or not self.bot_username:
            return
        now = int(time.time())
        settings = profile_settings()
        try:
            last_sent = int(settings.get("onboarding_start_hint_sent_at") or 0)
        except Exception:
            last_sent = 0
        if last_sent and now - last_sent < self._owner_start_notice_window:
            return
        bot = self.bot_username.lstrip("@")
        if settings.get("language") == "ru":
            text = (
                "DeathTG \u0436\u0434\u0435\u0442 \u0437\u0430\u043f\u0443\u0441\u043a inline-\u0431\u043e\u0442\u0430.\n\n"
                "Telegram \u043d\u0435 \u0440\u0430\u0437\u0440\u0435\u0448\u0430\u0435\u0442 \u0431\u043e\u0442\u0443 \u043f\u0435\u0440\u0432\u044b\u043c \u043f\u0438\u0441\u0430\u0442\u044c \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0443. \u041e\u0442\u043a\u0440\u043e\u0439 inline-\u0431\u043e\u0442\u0430 \u0438 \u043d\u0430\u0436\u043c\u0438 Start:\n"
                f"https://t.me/{bot}?start=owner\n\n"
                "\u041f\u043e\u0441\u043b\u0435 /start DeathTG \u043f\u043e\u043a\u0430\u0436\u0435\u0442 \u0432\u044b\u0431\u043e\u0440 \u044f\u0437\u044b\u043a\u0430 \u0438 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0443 backup \u0443\u0436\u0435 \u0442\u0430\u043c."
            )
        else:
            text = (
                "DeathTG inline setup is waiting.\n\n"
                "Telegram does not allow a bot to message you first. Open the inline bot and press Start:\n"
                f"https://t.me/{bot}?start=owner\n\n"
                "After /start DeathTG will show language and backup setup there."
            )
        try:
            await self.user_client.send_message("me", text, link_preview=False)
            save_profile_settings(onboarding_start_hint_sent_at=str(now))
        except Exception:
            pass

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
        icon = premium_emoji("pirate", self.owner_premium)
        if self.owner_id and self.owner_username:
            return f"{icon} Owner: {self.owner_id} (@{self.owner_username})"
        if self.owner_id:
            return f"{icon} Owner: {self.owner_id}"
        return f"{icon} Owner: unknown"

    @staticmethod
    def _is_private_message(event) -> bool:
        if not getattr(event, "is_private", False):
            return False
        text = (getattr(event, "raw_text", "") or "").strip().lower()
        return text not in {"/start", "/status", "/lang"}

    def _current_language(self) -> str:
        return profile_settings().get("language", "en")

    def _t(self, key: str, lang: str | None = None, **kwargs) -> str:
        return translate(key, lang or self._current_language(), **kwargs)

    def _owner_display_name(self) -> str:
        if self.owner_username:
            return f"@{self.owner_username}"
        if self.owner_id:
            return str(self.owner_id)
        return "DeathTG operator"

    @staticmethod
    def _backup_interval_label(minutes: int) -> str:
        if minutes < 60:
            return f"{minutes}m"
        return f"{minutes // 60}h"

    def _help_buttons_native(self):
        return [[
            Button.url(self._t("bot.news"), "https://t.me/Death_Telega"),
            Button.url(self._t("bot.support"), "https://t.me/Death_TgOfftop"),
        ]]

    def _help_buttons_form(self):
        return [[
            {"text": self._t("bot.news"), "url": "https://t.me/Death_Telega"},
            {"text": self._t("bot.support"), "url": "https://t.me/Death_TgOfftop"},
        ]]

    def _owner_panel_buttons_native(self) -> list[list]:
        if not self.owner_id:
            return self._help_buttons_native()
        link = issue_device_grant("Telegram /start", created_by="inline_start", owner_id=int(self.owner_id))
        return [
            [Button.url(self._t("bot.open"), link)],
            *self._help_buttons_native(),
        ]

    def _owner_panel_buttons_form(self) -> list[list]:
        if not self.owner_id:
            return self._help_buttons_form()
        link = issue_device_grant("Telegram /start", created_by="inline_start", owner_id=int(self.owner_id))
        rows: list[list] = [
            [{"text": self._t("bot.open"), "url": link}],
            [{"text": self._t("bot.change_language"), "callback": self._language_settings_callback, "args": ()}],
        ]
        rows.extend(self._help_buttons_form())
        return rows

    async def _send_language_picker(self, chat_id: int, *, onboarding: bool) -> None:
        owner = self._owner_display_name()
        text = (
            emoji_line("pirate", f"\u0417\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439\u0442\u0435, {owner}", self.owner_premium)
            + "\n"
            + emoji_line("heart", f"God Bless America, {owner}", self.owner_premium)
            + "\n\n"
            + emoji_line("phone", "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u044f\u0437\u044b\u043a / Choose language", self.owner_premium)
        )
        await self.push_form(
            chat_id,
            text,
            reply_markup=[
                [
                    {"text": "Русский", "callback": self._language_picker_callback, "args": ("ru", "1" if onboarding else "0")},
                    {"text": "English", "callback": self._language_picker_callback, "args": ("en", "1" if onboarding else "0")},
                ],
            ],
            ttl=60 * 60 * 24,
            parse_mode="html",
            photo=str(system_image("welcome")) if system_image("welcome") else None,
        )

    async def ensure_owner_onboarding(self) -> None:
        settings = profile_settings()
        if not self.owner_id or settings.get("onboarding_done") == "1":
            return
        if not self.bot_client:
            return
        await self._send_language_picker(int(self.owner_id), onboarding=True)

    async def _send_backup_prompt(self, chat_id: int, language: str) -> None:
        lang = "ru" if language == "ru" else "en"
        await self.push_form(
            chat_id,
            emoji_line("check", self._t("bot.language_saved", lang), self.owner_premium) + "\n\n" + emoji_line("sync", self._t("bot.backup", lang), self.owner_premium),
            reply_markup=[
                [{"text": self._t("bot.yes", lang), "callback": self._onboarding_backup_toggle_callback, "args": (lang, "1")}],
                [{"text": self._t("bot.no", lang), "callback": self._onboarding_backup_toggle_callback, "args": (lang, "0")}],
            ],
            ttl=60 * 60 * 24,
            parse_mode="html",
            photo=str(system_image("creating_backup")) if system_image("creating_backup") else None,
        )

    async def _language_settings_callback(self, call) -> None:
        await self._send_language_picker(int(call.chat_id), onboarding=False)
        with contextlib.suppress(Exception):
            await call.delete()

    async def _language_picker_callback(self, call, language: str, onboarding: str) -> None:
        lang = "ru" if language == "ru" else "en"
        save_profile_settings(language=lang)
        if onboarding == "1":
            await call.edit(
                emoji_line("check", self._t("bot.language_saved", lang), self.owner_premium)
                + "\n\n"
                + emoji_line("sync", self._t("bot.backup", lang), self.owner_premium),
                reply_markup=[
                    [
                        {"text": self._t("bot.yes", lang), "callback": self._onboarding_backup_toggle_callback, "args": (lang, "1")},
                        {"text": self._t("bot.no", lang), "callback": self._onboarding_backup_toggle_callback, "args": (lang, "0")},
                    ],
                ],
                parse_mode="html",
                ttl=60 * 60 * 24,
            )
            return
        with contextlib.suppress(Exception):
            await call.delete()
        await self.push_form(
            int(call.chat_id),
            self._t("bot.language_saved", lang),
            reply_markup=self._owner_panel_buttons_form() if self.owner_id and int(call.chat_id) == self.owner_id else self._help_buttons_form(),
            ttl=60 * 60,
            parse_mode="html",
        )

    async def _onboarding_backup_toggle_callback(self, call, language: str, enabled: str) -> None:
        lang = "ru" if language == "ru" else "en"
        if enabled != "1":
            save_profile_settings(language=lang, backup_enabled="0", onboarding_done="1")
            await call.edit(emoji_line("heart", self._t("bot.backup_off", lang), self.owner_premium) + "\n\n" + emoji_line("check", self._t("bot.ready", lang), self.owner_premium), reply_markup=None, parse_mode="html")
            return
        rows = []
        labels = [10, 30, 60, 120, 180, 360, 720, 1440]
        for start_row in range(0, len(labels), 3):
            row = []
            for minutes in labels[start_row:start_row + 3]:
                row.append(
                    {
                        "text": self._backup_interval_label(minutes),
                        "callback": self._onboarding_backup_interval_callback,
                        "args": (lang, str(minutes)),
                    }
                )
            rows.append(row)
        rows.append([{"text": self._t("bot.never", lang), "callback": self._onboarding_backup_interval_callback, "args": (lang, "0")}])
        await call.edit(emoji_line("sync", self._t("bot.choose_interval", lang), self.owner_premium), reply_markup=rows, parse_mode="html")

    async def _onboarding_backup_interval_callback(self, call, language: str, minutes: str) -> None:
        lang = "ru" if language == "ru" else "en"
        enabled = "1" if minutes not in {"", "0"} else "0"
        interval_minutes = int(minutes) if minutes.isdigit() and int(minutes) > 0 else 1440
        interval_hours = max(1, interval_minutes // 60)
        save_profile_settings(
            language=lang,
            backup_enabled=enabled,
            backup_interval_minutes=str(interval_minutes),
            backup_interval_hours=str(interval_hours),
            onboarding_done="1",
            backup_last_sent_at="0",
        )
        if enabled == "1":
            backup_note = ""
            try:
                result = await asyncio.to_thread(create_modules_backup, "onboarding")
                path = str(result.get("path") or "")
                if path and self.user_client:
                    await self.user_client.send_file(
                        "me",
                        path,
                        caption=(
                            f"{premium_emoji('inbox', self.owner_premium)} DeathTG backup\n"
                            f"Modules: {result.get('module_count', 0)}\n"
                            f"Files: {result.get('file_count', 0)}"
                        ),
                    )
                    save_profile_settings(backup_last_sent_at=str(int(time.time())), backup_last_path=path)
                    backup_note = "\n" + emoji_line("mail", self._t("bot.backup_file_sent", lang), self.owner_premium)
            except Exception as exc:
                backup_note = "\n" + emoji_line("alert", f"Backup file error: {type(exc).__name__}: {exc}", self.owner_premium)
            label = self._backup_interval_label(interval_minutes)
            message = f"{emoji_line('check', self._t('bot.backup_saved', lang), self.owner_premium)}\n\n{emoji_line('sync', self._t('bot.interval', lang), self.owner_premium)}: {label}{backup_note}\n\n{emoji_line('pirate', self._t('bot.ready', lang), self.owner_premium)}"
        else:
            message = emoji_line("heart", self._t("bot.backup_off", lang), self.owner_premium) + "\n\n" + emoji_line("check", self._t("bot.ready", lang), self.owner_premium)
        await call.edit(message, reply_markup=None, parse_mode="html")

    async def _on_start(self, event) -> None:
        settings = profile_settings()
        if self.owner_id and int(getattr(event, "sender_id", 0) or 0) == self.owner_id and settings.get("onboarding_done") != "1":
            await self._send_language_picker(event.chat_id, onboarding=True)
            return
        lang = settings.get("language", "en")
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        if self.owner_id and sender_id == self.owner_id:
            remote_hint = self._t("bot.link_phone_pc", lang) if panel_remote_access_ready() else self._t("bot.link_reachable", lang)
            text = (
                emoji_line("pirate", self._t("bot.ready", lang), self.owner_premium) + "\n\n"
                + emoji_line("mail", self._t("bot.private_link_attached", lang), self.owner_premium) + "\n"
                + emoji_line("key", self._t("bot.do_not_share", lang), self.owner_premium) + "\n"
                + emoji_line("phone", remote_hint, self.owner_premium) + "\n\n"
                + emoji_line("sync", self._t("bot.lang_help", lang), self.owner_premium) + "\n\n"
                + f"{self._owner_line()}\n"
                + f"Bot: @{self.bot_username or 'unknown'}"
            )
            await event.respond(text, buttons=self._owner_panel_buttons_native(), parse_mode="html", link_preview=False)
            return
        text = (
            emoji_line("pirate", self._t("bot.ready", lang), self.owner_premium) + "\n\n"
            + f"{self._owner_line()}\n"
            + f"Bot: @{self.bot_username or 'unknown'}\n\n"
            + f"{emoji_line('search', self._t('bot.commands', lang), self.owner_premium)}:\n"
            + emoji_line("mail", self._t("bot.start_help", lang), self.owner_premium) + "\n"
            + emoji_line("check", self._t("bot.status_help", lang), self.owner_premium) + "\n"
            + emoji_line("sync", self._t("bot.lang_help", lang), self.owner_premium)
        )
        await event.respond(text, buttons=self._help_buttons_native(), parse_mode="html", link_preview=False)

    async def _on_status(self, event) -> None:
        lang = self._current_language()
        ready = self._t("bot.runtime_ready", lang) if self.ready else self._t("bot.runtime_missing", lang)
        text = (
            emoji_line("check", self._t("bot.status", lang), self.owner_premium) + "\n"
            + f"{emoji_line('check', 'ready', self.owner_premium)}: {ready}\n"
            + f"callbacks: {len(self.registry)}\n"
            + f"{self._owner_line()}"
        )
        await event.respond(text, buttons=self._help_buttons_native(), parse_mode="html", link_preview=False)

    async def _on_lang(self, event) -> None:
        settings = profile_settings()
        if self.owner_id and int(getattr(event, "sender_id", 0) or 0) == self.owner_id and settings.get("onboarding_done") != "1":
            await self._send_language_picker(event.chat_id, onboarding=True)
            return
        await self._send_language_picker(event.chat_id, onboarding=False)

    async def _on_private_message(self, event) -> None:
        lang = self._current_language()
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        if self.owner_id and sender_id == self.owner_id:
            await event.respond(
                emoji_line("mail", self._t("bot.private", lang), self.owner_premium) + "\n\n" + emoji_line("key", self._t("bot.use_button", lang), self.owner_premium),
                buttons=self._owner_panel_buttons_native(),
                parse_mode="html",
                link_preview=False,
            )
            return
        await event.respond(emoji_line("mail", self._t("bot.private", lang), self.owner_premium), buttons=self._help_buttons_native(), parse_mode="html", link_preview=False)
