from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
from pathlib import Path

from telethon import TelegramClient, events

from deathtg.assets import system_image
from deathtg.config import DeathTGConfig, MODULES_DIR, RUNTIME_DIR
from deathtg.inline import InlineManager
from deathtg.loader import ModuleLoader
from deathtg.metrics import init_metrics, record_command
from deathtg.permissions import SecurityManager
from deathtg.registry import CommandRegistry, PROTECTED_MODULES
from deathtg.startup_sync import run_startup_sync
from deathtg.update_manager import (
    apply_update,
    ignore_update,
    inspect_update,
    mark_update_notified,
    save_update_state,
    schedule_restart,
    should_notify_update,
    update_notify_enabled,
    update_notify_interval,
)
from deathtg.ui import CONSOLE_BANNER, fail

log = logging.getLogger("deathtg")
CORE_MODULES = ["core", "root", "info", "system", "antivirus", "terminal"]
PANEL_ACTIONS_PATH = RUNTIME_DIR / "panel_actions.jsonl"
MODULE_META_PATH = RUNTIME_DIR / "module_meta.json"


class DeathTG:
    def __init__(self, config: DeathTGConfig) -> None:
        self.config = config
        self.client = TelegramClient(config.session_name, config.api_id, config.api_hash)
        self.client.deathtg_app = self
        self.registry = CommandRegistry()
        self.loader = ModuleLoader(self.registry, MODULES_DIR)
        self.security = SecurityManager()
        self.inline = InlineManager(api_id=config.api_id, api_hash=config.api_hash, user_client=self.client)
        self.loader.bind(app=self, client=self.client, inline_manager=self.inline)
        self._force_loaded_modules: set[str] = set()
        self._panel_action_pos = PANEL_ACTIONS_PATH.stat().st_size if PANEL_ACTIONS_PATH.exists() else 0
        self._panel_actions_task: asyncio.Task | None = None
        self._update_watch_task: asyncio.Task | None = None

    async def start(self) -> None:
        print(CONSOLE_BANNER)
        await init_metrics()
        await self.client.start()

        me = await self.client.get_me()
        if self.config.owner_id is None:
            self.config.owner_id = me.id

        self._write_runtime_profile(me)
        try:
            await run_startup_sync(self.client)
        except Exception:
            log.exception("Startup sync failed")
        await self.inline.start()

        await self.loader.load_builtin("deathtg.modules", CORE_MODULES)
        await self.loader.load_all_local(force_modules=self._force_modules())

        self._panel_action_pos = 0
        self._panel_actions_task = asyncio.create_task(self._panel_actions_loop())
        self._update_watch_task = asyncio.create_task(self._update_watch_loop())

        self.client.add_event_handler(self._dispatch, events.NewMessage())
        self.client.add_event_handler(self._dispatch_watchers, events.NewMessage())
        log.info("DeathTG started as @%s", getattr(me, "username", None) or me.id)
        try:
            await self.client.run_until_disconnected()
        finally:
            if self._panel_actions_task:
                self._panel_actions_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._panel_actions_task
            if self._update_watch_task:
                self._update_watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._update_watch_task
            await self.inline.stop()

    def _write_runtime_profile(self, me) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        name = " ".join([getattr(me, "first_name", "") or "", getattr(me, "last_name", "") or ""]).strip()
        data = {
            "id": str(getattr(me, "id", "unknown")),
            "name": name or "DeathTG User",
            "username": getattr(me, "username", None) or "",
            "ok": "1",
        }
        (RUNTIME_DIR / "profile.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _dispatch(self, event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        prefix = self.config.command_prefix
        if not text.startswith(prefix):
            return

        raw = text[len(prefix):].strip()
        if not raw:
            return

        command_name, *args = raw.split()
        command = self.registry.get(command_name)
        if command is None:
            return
        if not await self.security.command_allowed(event, command, self.config.owner_id):
            return

        try:
            await record_command(command.module, command.name)
            await command.handler(event, args)
        except Exception as exc:
            log.exception("Command failed: %s", command.name)
            await event.edit(
                fail(f"Error in .{command.name}: <code>{type(exc).__name__}: {exc}</code>"),
                parse_mode="html",
            )

    async def _dispatch_watchers(self, event: events.NewMessage.Event) -> None:
        for module_name, handlers in list(self.loader.watchers.items()):
            for handler, meta in list(handlers):
                if not self._watcher_allows(event, meta):
                    continue
                try:
                    await handler(event)
                except Exception:
                    log.exception("Watcher failed: %s", module_name)

    def _watcher_allows(self, event: events.NewMessage.Event, meta: dict) -> bool:
        tags = set(meta.get("tags") or ())
        filters = dict(meta.get("filters") or {})
        text = getattr(event, "raw_text", "") or ""
        if ("out" in tags or filters.get("out")) and not getattr(event, "out", False):
            return False
        if ("in" in tags or filters.get("in") or filters.get("incoming")) and getattr(event, "out", False):
            return False
        if ("only_commands" in tags or filters.get("only_commands")) and not text.startswith(self.config.command_prefix):
            return False
        if ("no_commands" in tags or filters.get("no_commands")) and text.startswith(self.config.command_prefix):
            return False
        contains = filters.get("contains")
        if contains and str(contains) not in text:
            return False
        return True

    async def _panel_actions_loop(self) -> None:
        while True:
            try:
                await self._read_panel_actions()
            except Exception:
                log.exception("Panel action sync failed")
            await asyncio.sleep(1.0)

    async def _update_watch_loop(self) -> None:
        while True:
            try:
                await self._check_updates_once()
            except Exception:
                log.exception("Update watch failed")
            await asyncio.sleep(update_notify_interval())

    async def _check_updates_once(self) -> None:
        if not update_notify_enabled() or not self.inline.ready or not self.config.owner_id:
            return
        info = await asyncio.to_thread(inspect_update)
        save_update_state(info)
        if should_notify_update(info):
            await self._send_update_notification(info)
            mark_update_notified(info)

    async def _send_update_notification(self, info: dict[str, object]) -> None:
        if not self.config.owner_id:
            return
        current = str(info.get("current") or "")[:10]
        upcoming = str(info.get("upcoming") or "")[:10]
        text = (
            "<b>Доступно обновление DeathTG</b>\n\n"
            f"Ветка: <code>{info.get('branch') or 'main'}</code>\n"
            f"Текущий билд: <code>{current}</code>\n"
            f"Новый билд: <code>{upcoming}</code>\n"
            f"Коммитов позади: <code>{info.get('behind') or 0}</code>\n\n"
            "Обновить сейчас или напомнить позже?"
        )
        photo = system_image("update_available")
        await self.inline.push_form(
            int(self.config.owner_id),
            text,
            reply_markup=[
                [{"text": "Обновить", "callback": self._update_apply_callback, "args": (str(info.get("upcoming") or ""),)}],
                [{"text": "Игнорировать", "callback": self._update_ignore_callback, "args": (str(info.get("upcoming") or ""),)}],
            ],
            ttl=60 * 60 * 24 * 7,
            parse_mode="html",
            photo=str(photo) if photo else None,
        )

    async def _update_apply_callback(self, call, expected_upcoming: str) -> None:
        await call.edit("<b>Обновляю DeathTG...</b>", reply_markup=None, parse_mode="html")
        result = await asyncio.to_thread(apply_update)
        message = html.escape(str(result.get("message") or "No output")[-3000:])
        if not result.get("ok"):
            await call.edit(
                f"<b>Обновление не удалось.</b>\n<pre>{message}</pre>",
                reply_markup=[[{"text": "Закрыть", "callback": self._close_callback, "args": ()}]],
                parse_mode="html",
            )
            return
        if result.get("updated"):
            await call.edit(
                f"<b>DeathTG обновлён.</b>\n<pre>{message}</pre>\nНажми перезагрузку, чтобы применить изменения.",
                reply_markup=[
                    [{"text": "Перезагрузить", "callback": self._restart_after_update_callback, "args": ()}],
                    [{"text": "Закрыть", "callback": self._close_callback, "args": ()}],
                ],
                parse_mode="html",
            )
            return
        await call.edit(
            f"<b>Уже актуально.</b>\n<pre>{message}</pre>",
            reply_markup=[[{"text": "Закрыть", "callback": self._close_callback, "args": ()}]],
            parse_mode="html",
        )

    async def _update_ignore_callback(self, call, expected_upcoming: str) -> None:
        await asyncio.to_thread(ignore_update, {"upcoming": expected_upcoming})
        await call.edit(
            "<b>Обновление скрыто.</b>\nDeathTG снова пришлёт уведомление, когда в репозитории появится уже другой билд.",
            reply_markup=None,
            parse_mode="html",
        )

    async def _restart_after_update_callback(self, call) -> None:
        schedule_restart()
        await call.edit(
            "<b>Перезагрузка запущена.</b>\nDeathTG поднимется снова через несколько секунд.",
            reply_markup=None,
            parse_mode="html",
        )

    async def _close_callback(self, call) -> None:
        await call.edit("Closed.", reply_markup=None)

    async def _read_panel_actions(self) -> None:
        if not PANEL_ACTIONS_PATH.exists():
            return
        size = PANEL_ACTIONS_PATH.stat().st_size
        if size < self._panel_action_pos:
            self._panel_action_pos = 0
        with PANEL_ACTIONS_PATH.open("r", encoding="utf-8") as f:
            f.seek(self._panel_action_pos)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                await self._apply_panel_action(payload)
            self._panel_action_pos = f.tell()

    async def _apply_panel_action(self, payload: dict) -> None:
        action = str(payload.get("action") or "").strip()
        if action == "install":
            raw_path = str(payload.get("path") or "")
            path = Path(raw_path)
            if path.exists():
                force = bool(payload.get("force"))
                await self.loader.load_file(path, force=force)
                if force:
                    self._force_loaded_modules.add(path.stem if path.is_file() else path.name)
                log.info("Panel sync installed: %s", path.name)
            return
        if action == "unload":
            module = str(payload.get("module") or "").strip()
            if module and module not in PROTECTED_MODULES:
                self.loader.unload(module, silent=True)
                log.info("Panel sync unloaded: %s", module)
            return
        if action == "delete":
            module = str(payload.get("module") or "").strip()
            if module and module not in PROTECTED_MODULES:
                self.loader.unload(module, silent=True)
                log.info("Panel sync deleted: %s", module)
            return
        if action == "reload_all":
            await self.loader.load_all_local(force_modules=self._force_modules())
            log.info("Panel sync reloaded all local modules")
            return
        if action == "reload_config":
            module = str(payload.get("module") or "").strip()
            if module and module in self.loader.loaded:
                for inst in self.loader.instances.get(module, []):
                    self.loader._load_config(inst, module)
                    await self.loader._call_hook(inst, "client_ready")
                log.info("Panel sync refreshed config: %s", module)
            return
        if action == "startup_sync":
            await run_startup_sync(self.client)
            await self.inline.stop()
            self.inline = InlineManager(api_id=self.config.api_id, api_hash=self.config.api_hash, user_client=self.client)
            await self.inline.start()
            self.loader.bind(app=self, client=self.client, inline_manager=self.inline)
            log.info("Panel sync refreshed startup state")

    def module_file(self, name: str) -> Path:
        safe_name = Path(name).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        return MODULES_DIR / safe_name

    def _force_modules(self) -> set[str]:
        if not MODULE_META_PATH.exists():
            return set(self._force_loaded_modules)
        try:
            data = json.loads(MODULE_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            return set(self._force_loaded_modules)
        if not isinstance(data, dict):
            return set(self._force_loaded_modules)
        forced = {
            name
            for name, item in data.items()
            if isinstance(item, dict) and (item.get("verified") or item.get("security_override"))
        }
        return forced | self._force_loaded_modules


def run_async(config: DeathTGConfig) -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon.network").setLevel(logging.WARNING)
    logging.getLogger("telethon.client.updates").setLevel(logging.WARNING)
    logging.getLogger("telethon.client.uploads").setLevel(logging.WARNING)
    bot = DeathTG(config)
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        print("DeathTG stopped.")
