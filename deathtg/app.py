from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from telethon import TelegramClient, events

from deathtg.config import DeathTGConfig, MODULES_DIR, RUNTIME_DIR
from deathtg.inline import InlineManager
from deathtg.loader import ModuleLoader
from deathtg.metrics import init_metrics, record_command
from deathtg.permissions import SecurityManager
from deathtg.registry import CommandRegistry, PROTECTED_MODULES
from deathtg.startup_sync import run_startup_sync
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
            if path.exists() and path.suffix == ".py":
                force = bool(payload.get("force"))
                await self.loader.load_file(path, force=force)
                if force:
                    self._force_loaded_modules.add(path.stem)
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
    bot = DeathTG(config)
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        print("DeathTG stopped.")
