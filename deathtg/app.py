from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import os
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UnauthorizedError,
    UserDeactivatedError,
    YouBlockedUserError,
)

from deathtg.assets import system_image
from deathtg.backup_manager import create_modules_backup
from deathtg.community_bot import CommunityBotService
from deathtg.community_roles import (
    community_enabled_for_owner,
    preferred_community_bot_username,
    write_role_scan_result,
)
from deathtg.config import DeathTGConfig, ENV_PATH, MODULES_DIR, ROOT_DIR, RUNTIME_DIR
from deathtg.inline import InlineManager
from deathtg.loader import ModuleLoader
from deathtg.metrics import init_metrics, record_command
from deathtg.permissions import SecurityManager
from deathtg.premium_emoji import FALLBACK_EMOJI
from deathtg.profile_store import profile_settings, save_profile_settings
from deathtg.health_tools import save_health_state
from deathtg.startup_state import (
    PHASE_DEGRADED,
    PHASE_POST_SETUP_SYNC,
    PHASE_READY,
    PHASE_REPAIR,
    PHASE_SAFE_MODE,
    write_startup_state,
)
from deathtg.registry import CommandRegistry, PROTECTED_MODULES
from deathtg.startup_sync import run_startup_sync
from deathtg.startup_sync import check_runtime_integrity
from deathtg.server_bootstrap import update_env_values
from deathtg.session_guard import session_files
from deathtg.telethon_policy import client_retry_kwargs, quiet_telethon_network_logs
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
from deathtg.ui import fail

log = logging.getLogger("deathtg")
CORE_MODULES = ["core", "root", "info", "system", "antivirus", "terminal"]
INVALID_SESSION_ERRORS = (
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UnauthorizedError,
    UserDeactivatedError,
)
PANEL_ACTIONS_PATH = RUNTIME_DIR / "panel_actions.jsonl"
MODULE_META_PATH = RUNTIME_DIR / "module_meta.json"
RUNTIME_LOG_PATH = RUNTIME_DIR / "deathtg.log"


class DeathTG:
    def __init__(self, config: DeathTGConfig) -> None:
        self.config = config
        self.client = TelegramClient(str(ROOT_DIR / config.session_name), config.api_id, config.api_hash, **client_retry_kwargs())
        self.client.deathtg_app = self
        self.registry = CommandRegistry()
        self.loader = ModuleLoader(self.registry, MODULES_DIR)
        self.security = SecurityManager()
        self.inline = InlineManager(api_id=config.api_id, api_hash=config.api_hash, user_client=self.client)
        self.community_bot = CommunityBotService(api_id=config.api_id, api_hash=config.api_hash, user_client=self.client)
        self.loader.bind(app=self, client=self.client, inline_manager=self.inline)
        self._force_loaded_modules: set[str] = set()
        self._panel_action_pos = self._panel_action_file_size()
        self._panel_actions_task: asyncio.Task | None = None
        self._update_watch_task: asyncio.Task | None = None
        self._integrity_watch_task: asyncio.Task | None = None
        self._backup_task: asyncio.Task | None = None
        self._bootstrap_task: asyncio.Task | None = None
        self.owner_premium: bool = False

    async def start(self) -> None:
        await init_metrics()
        try:
            await self.client.connect()
            authorized = await self.client.is_user_authorized()
        except INVALID_SESSION_ERRORS as exc:
            self._handle_invalid_runtime_session(exc)
            await self.client.disconnect()
            return
        if not authorized:
            self._invalidate_broken_session()
            write_startup_state(
                PHASE_DEGRADED,
                "Telegram session is missing or invalid. Open setup and reconnect the account.",
            )
            save_health_state(
                last_action={
                    "kind": "session_invalid",
                    "ok": False,
                    "message": "Stored Telegram session is missing or invalid. DeathTG switched back to setup mode.",
                }
            )
            log.error("Stored Telegram session is missing or invalid; refusing interactive login prompt")
            await self.client.disconnect()
            return

        try:
            me = await self.client.get_me()
        except INVALID_SESSION_ERRORS as exc:
            self._handle_invalid_runtime_session(exc)
            await self.client.disconnect()
            return
        self.owner_premium = bool(getattr(me, "premium", False))
        if self.config.owner_id is None:
            self.config.owner_id = me.id

        self._write_runtime_profile(me)
        await self.loader.load_builtin("deathtg.modules", CORE_MODULES)
        if self.config.safe_mode:
            write_startup_state(PHASE_SAFE_MODE, "DeathTG started in safe mode. External local modules were skipped.")
            save_health_state(
                safe_mode=True,
                last_action={
                    "kind": "safe_mode_boot",
                    "ok": True,
                    "message": "DeathTG started in safe mode. External local modules were skipped.",
                },
            )
            log.warning("DeathTG started in safe mode; external local modules are skipped")
        else:
            write_startup_state(PHASE_POST_SETUP_SYNC, "DeathTG is loading local modules and preparing runtime services.")
            await self.loader.load_all_local(force_modules=self._force_modules())

        self.client.add_event_handler(self._dispatch, events.NewMessage())
        self.client.add_event_handler(self._dispatch_watchers, events.NewMessage())

        # Do not replay stale panel actions after a restart. The panel appends
        # fresh actions to this JSONL file, and the userbot tails only new rows.
        self._panel_action_pos = self._panel_action_file_size()
        self._panel_actions_task = asyncio.create_task(self._panel_actions_loop())
        self._update_watch_task = asyncio.create_task(self._update_watch_loop())
        self._integrity_watch_task = asyncio.create_task(self._integrity_watch_loop())
        self._backup_task = asyncio.create_task(self._backup_loop())
        self._bootstrap_task = asyncio.create_task(self._bootstrap_services())
        log.info("%s DeathTG started as @%s", FALLBACK_EMOJI["check"], getattr(me, "username", None) or me.id)
        try:
            await self.client.run_until_disconnected()
        except INVALID_SESSION_ERRORS as exc:
            self._handle_invalid_runtime_session(exc)
        finally:
            if self._bootstrap_task:
                self._bootstrap_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._bootstrap_task
            if self._panel_actions_task:
                self._panel_actions_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._panel_actions_task
            if self._update_watch_task:
                self._update_watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._update_watch_task
            if self._integrity_watch_task:
                self._integrity_watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._integrity_watch_task
            if self._backup_task:
                self._backup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._backup_task
            await self.community_bot.stop()
            await self.inline.stop()

    def _handle_invalid_runtime_session(self, exc: BaseException) -> None:
        reason = type(exc).__name__
        message = f"Telegram session expired or was revoked ({reason}). Open setup and reconnect the account."
        self._invalidate_broken_session()
        write_startup_state(PHASE_DEGRADED, message)
        save_health_state(
            last_action={
                "kind": "session_invalid_runtime",
                "ok": False,
                "message": message,
            }
        )
        log.error("Telegram session became invalid; DeathTG switched back to setup mode: %s", exc)

    def _invalidate_broken_session(self) -> None:
        for path in session_files(self.config.session_name):
            if not path.exists():
                continue
            backup = path.with_suffix(path.suffix + ".invalid")
            try:
                if backup.exists():
                    backup.unlink()
                path.replace(backup)
            except Exception:
                with contextlib.suppress(Exception):
                    path.unlink()
        self._mark_profile_session_invalid()
        update_env_values({"LOGIN_PENDING": "1", "LOGIN_STAGE": "idle"}, path=ENV_PATH)
        os.environ["LOGIN_PENDING"] = "1"
        os.environ["LOGIN_STAGE"] = "idle"

    def _mark_profile_session_invalid(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNTIME_DIR / "profile.json"
        current: dict[str, str] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    current = data
            except Exception:
                current = {}
        current["ok"] = "0"
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    async def _bootstrap_services(self) -> None:
        if self.config.safe_mode:
            log.warning("Skipping startup sync, inline and community bootstrap because safe mode is enabled")
            return
        write_startup_state(PHASE_POST_SETUP_SYNC, "DeathTG is running startup sync and recovering Telegram resources.")
        try:
            await asyncio.wait_for(run_startup_sync(self.client), timeout=180)
        except asyncio.TimeoutError:
            write_startup_state(PHASE_DEGRADED, "Startup sync timed out after 180 seconds.")
            log.error("Startup sync timed out after 180 seconds")
        except Exception:
            write_startup_state(PHASE_DEGRADED, "Startup sync failed. Check runtime logs for details.")
            log.exception("Startup sync failed")

        try:
            await self.inline.start()
        except Exception:
            write_startup_state(PHASE_DEGRADED, "Inline manager failed to start.")
            log.exception("Inline manager failed to start")

        try:
            await self.community_bot.start(int(self.config.owner_id or 0))
        except Exception:
            write_startup_state(PHASE_DEGRADED, "Community bot failed to start.")
            log.exception("Community bot failed to start")

        try:
            await self.inline.ensure_owner_onboarding()
        except Exception:
            log.exception("Owner onboarding failed")
        try:
            integrity = await check_runtime_integrity(self.client, notify=False, allow_repair=False)
            failures = []
            for item in list(integrity.get("bots") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("error") or not item.get("configured") or not item.get("valid_username") or not item.get("start_ping"):
                    failures.append(str(item.get("role") or "bot"))
            if failures:
                write_startup_state(PHASE_DEGRADED, f"Runtime started with degraded Telegram resources: {', '.join(failures)}.")
            else:
                write_startup_state(PHASE_READY, "Panel and userbot are ready.")
        except Exception:
            write_startup_state(PHASE_DEGRADED, "Runtime finished booting, but integrity verification failed.")
            log.exception("Post-start integrity verification failed")

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

    async def _integrity_watch_loop(self) -> None:
        while True:
            try:
                if self._bootstrap_task and not self._bootstrap_task.done():
                    await asyncio.sleep(5)
                    continue
                await check_runtime_integrity(self.client, notify=True)
            except Exception:
                log.exception("Integrity watch failed")
            await asyncio.sleep(300)

    async def _backup_loop(self) -> None:
        while True:
            try:
                settings = profile_settings()
                if settings.get("backup_enabled") != "1":
                    await asyncio.sleep(60)
                    continue
                interval_minutes = self._backup_interval_minutes(settings)
                last_sent = int(settings.get("backup_last_sent_at") or 0)
                now = int(time.time())
                if now - last_sent < interval_minutes * 60:
                    await asyncio.sleep(min(300, max(30, interval_minutes * 60 - (now - last_sent))))
                    continue
                result = await asyncio.to_thread(create_modules_backup, "scheduled")
                path = str(result.get("path") or "")
                if path:
                    caption = (
                        f"{FALLBACK_EMOJI['inbox']} DeathTG backup\n"
                        f"Modules: {result.get('module_count', 0)}\n"
                        f"Files: {result.get('file_count', 0)}"
                    )
                    await self.client.send_file("me", path, caption=caption)
                    save_profile_settings(
                        backup_last_sent_at=str(now),
                        backup_last_path=path,
                    )
            except Exception:
                log.exception("Backup loop failed")
                await asyncio.sleep(120)

    @staticmethod
    def _backup_interval_minutes(settings: dict[str, str]) -> int:
        raw_minutes = str(settings.get("backup_interval_minutes") or "").strip()
        if raw_minutes.isdigit() and int(raw_minutes) > 0:
            return max(10, int(raw_minutes))
        raw_hours = str(settings.get("backup_interval_hours") or "24").strip()
        if raw_hours.isdigit() and int(raw_hours) > 0:
            return max(10, int(raw_hours) * 60)
        return 24 * 60

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
            "<b>DeathTG update available</b>\n\n"
            f"Branch: <code>{info.get('branch') or 'main'}</code>\n"
            f"Current build: <code>{current}</code>\n"
            f"New build: <code>{upcoming}</code>\n"
            f"Commits behind: <code>{info.get('behind') or 0}</code>\n\n"
            "Update now or ignore for later?"
        )
        photo = system_image("update_available")
        await self.inline.push_form(
            int(self.config.owner_id),
            text,
            reply_markup=[
                [{"text": "Update", "callback": self._update_apply_callback, "args": (str(info.get("upcoming") or ""),)}],
                [{"text": "Ignore", "callback": self._update_ignore_callback, "args": (str(info.get("upcoming") or ""),)}],
            ],
            ttl=60 * 60 * 24 * 7,
            parse_mode="html",
            photo=str(photo) if photo else None,
        )

    async def _update_apply_callback(self, call, expected_upcoming: str) -> None:
        await call.edit("<b>Updating DeathTG...</b>", reply_markup=None, parse_mode="html")
        result = await asyncio.to_thread(apply_update)
        message = html.escape(str(result.get("message") or "No output")[-3000:])
        if not result.get("ok"):
            await call.edit(
                f"<b>Update failed.</b>\n<pre>{message}</pre>",
                reply_markup=[[{"text": "Close", "callback": self._close_callback, "args": ()}]],
                parse_mode="html",
            )
            return
        if result.get("updated"):
            await call.edit(
                f"<b>DeathTG updated.</b>\n<pre>{message}</pre>\nPress restart to apply the new build.",
                reply_markup=[
                    [{"text": "Restart", "callback": self._restart_after_update_callback, "args": ()}],
                    [{"text": "Close", "callback": self._close_callback, "args": ()}],
                ],
                parse_mode="html",
            )
            return
        await call.edit(
            f"<b>Already up to date.</b>\n<pre>{message}</pre>",
            reply_markup=[[{"text": "Close", "callback": self._close_callback, "args": ()}]],
            parse_mode="html",
        )

    async def _update_ignore_callback(self, call, expected_upcoming: str) -> None:
        await asyncio.to_thread(ignore_update, {"upcoming": expected_upcoming})
        await call.edit(
            "<b>Update hidden.</b>\nDeathTG will notify you again when a different build appears in the repository.",
            reply_markup=None,
            parse_mode="html",
        )

    async def _restart_after_update_callback(self, call) -> None:
        schedule_restart()
        await call.edit(
            "<b>Restart scheduled.</b>\nDeathTG will boot again in a few seconds.",
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

    @staticmethod
    def _panel_action_file_size() -> int:
        try:
            return PANEL_ACTIONS_PATH.stat().st_size if PANEL_ACTIONS_PATH.exists() else 0
        except OSError:
            return 0

    async def _resolve_self_user_id(self) -> int:
        me = await self.client.get_me()
        return int(getattr(me, "id", 0) or 0)

    async def _verify_role_with_community_bot(self, role: str) -> tuple[bool, str]:
        user_id = await self._resolve_self_user_id()
        if community_enabled_for_owner(user_id):
            return True, "Owner access confirmed."
        username = (os.getenv("COMMUNITY_BOT_USERNAME", "") or preferred_community_bot_username()).strip().lstrip("@")
        if not username:
            return False, "Community bot username is not configured."
        try:
            entity = await self.client.get_entity(username)
        except Exception as exc:
            return False, f"DeathTG owner service is offline or unavailable. Wait for the owner to come online and try again: {exc}"
        try:
            async with self.client.conversation(entity, timeout=18, exclusive=False) as conv:
                await conv.send_message(f"/scan {user_id} {role}")
                response = await conv.get_response()
        except YouBlockedUserError:
            return False, "Unblock the DeathTG community bot in Telegram and try again."
        except Exception as exc:
            return False, f"DeathTG owner service did not respond. Wait for the owner to come online and try again: {exc}"
        text = (getattr(response, "raw_text", "") or "").strip().lower()
        if text == "true":
            return True, "Community bot approved the role."
        return False, "Role key is not activated. Ask the DeathTG owner for a one-time key and send it to the community bot."

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
            await self.community_bot.stop()
            await self.inline.stop()
            self.inline = InlineManager(api_id=self.config.api_id, api_hash=self.config.api_hash, user_client=self.client)
            await self.inline.start()
            await self.community_bot.start(int(self.config.owner_id or 0))
            self.loader.bind(app=self, client=self.client, inline_manager=self.inline)
            log.info("Panel sync refreshed startup state")
            return
        if action == "health_recheck":
            write_startup_state(PHASE_REPAIR, "DeathTG is running a health recheck.")
            status = await check_runtime_integrity(self.client, notify=False, allow_repair=False)
            save_health_state(
                last_action={
                    "kind": "recheck",
                    "ok": True,
                    "message": "Integrity recheck finished.",
                    "status": status,
                },
                last_integrity=status,
            )
            failures = []
            for item in list(status.get("bots") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("error") or not item.get("configured") or not item.get("valid_username") or not item.get("start_ping"):
                    failures.append(str(item.get("role") or "bot"))
            write_startup_state(
                PHASE_DEGRADED if failures else PHASE_READY,
                "Integrity recheck finished with problems." if failures else "Integrity recheck finished successfully.",
            )
            log.info("Health recheck finished")
            return
        if action == "health_repair":
            write_startup_state(PHASE_REPAIR, "DeathTG is running repair flow.")
            sync_status = await run_startup_sync(self.client)
            integrity_status = await check_runtime_integrity(self.client, notify=False, allow_repair=False)
            save_health_state(
                last_action={
                    "kind": "repair",
                    "ok": True,
                    "message": "Repair flow finished.",
                    "sync": sync_status,
                    "integrity": integrity_status,
                },
                last_sync=sync_status,
                last_integrity=integrity_status,
            )
            failures = []
            for item in list(integrity_status.get("bots") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("error") or not item.get("configured") or not item.get("valid_username") or not item.get("start_ping"):
                    failures.append(str(item.get("role") or "bot"))
            write_startup_state(
                PHASE_DEGRADED if failures else PHASE_READY,
                "Repair flow finished with remaining issues." if failures else "Repair flow finished successfully.",
            )
            log.info("Health repair flow finished")
            return
        if action == "role_scan":
            request_id = str(payload.get("request_id") or "").strip()
            role = str(payload.get("role") or "").strip().lower()
            if not request_id or role not in {"admin", "developer"}:
                return
            ok, message = await self._verify_role_with_community_bot(role)
            write_role_scan_result(request_id, ok=ok, message=message, role=role)

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
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    console_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(RUNTIME_LOG_PATH, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        handlers=[console_handler, file_handler],
        force=True,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon.client.updates").setLevel(logging.WARNING)
    logging.getLogger("telethon.client.uploads").setLevel(logging.WARNING)
    quiet_telethon_network_logs()
    bot = DeathTG(config)
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        print("DeathTG stopped.")
