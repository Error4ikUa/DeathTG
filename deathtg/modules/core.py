from __future__ import annotations

import html
from pathlib import Path
import re

from deathtg.command import command
from deathtg.config import MODULES_DIR, ROOT_DIR, RUNTIME_DIR
from deathtg.loader import Module
from deathtg.module_repo import fetch_repo_modules, find_repo_module, is_url, normalize_github_raw_url, trusted_repo_link
from deathtg.permissions import parse_security
from deathtg.profile_store import update_env_value
from deathtg.registry import PROTECTED_MODULES
from deathtg.startup_sync import (
    BOT_TOKEN_RE,
    check_runtime_integrity,
    manual_bot_blueprint,
    render_integrity_report,
    render_manual_bot_guide,
    run_startup_sync,
)
from deathtg.update_manager import apply_update


HELP_PAGE_SIZE = 8
DLMOD_PAGE_SIZE = 5


def _cmd(name: str) -> str:
    return f"<code>.{html.escape(name)}</code>"


def _is_trusted_dtg_link(link: str) -> bool:
    raw = (link or "").strip().lower()
    return (
        "raw.githubusercontent.com/error4ikua/dtg_modules/" in raw
        or "github.com/error4ikua/dtg_modules/" in raw
        or "api.github.com/repos/error4ikua/dtg_modules/" in raw
    )


class CoreMod(Module):
    strings = {"name": "core"}

    @command("help", description="DeathTG command list", usage=".help [module|all]", aliases=("h",), security="owner")
    async def help_cmd(self, event, args):
        if args:
            target = args[0].strip()
            if target.lower() == "all":
                await event.edit(self._help_all_text(), parse_mode="html", link_preview=False)
                return
            await event.edit(self._module_help_text(target), parse_mode="html", link_preview=False)
            return
        await event.edit(self._help_all_text(), parse_mode="html", link_preview=False)

    @command("helpb", description="Button-based DeathTG help", usage=".helpb [module]", aliases=("hb",), security="owner")
    async def helpb_cmd(self, event, args):
        if args:
            await self._send_module_help(event, args[0], page=0)
            return
        await self.inline_send(
            event,
            self._helpb_index_text(0),
            reply_markup=self._helpb_index_buttons(0),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    @command("modules", description="Show loaded modules", usage=".modules", security="owner")
    async def modules_cmd(self, event, args):
        await event.edit(self._modules_text(), parse_mode="html", link_preview=False)

    @command("sudolist", description="Show sudo users", usage=".sudolist", security="owner")
    async def sudolist_cmd(self, event, args):
        users = self.app.security.list_sudo_users()
        if not users:
            await event.edit("<b>Sudo users:</b>\n<code>none</code>", parse_mode="html")
            return
        lines = ["<b>Sudo users:</b>"] + [f"<code>{user_id}</code>" for user_id in users]
        await event.edit("\n".join(lines), parse_mode="html")

    @command("sudoadd", description="Add sudo user id", usage=".sudoadd 123456789", security="owner")
    async def sudoadd_cmd(self, event, args):
        if not args:
            await event.edit("<b>Usage:</b> <code>.sudoadd user_id</code>", parse_mode="html")
            return
        try:
            user_id = int(args[0])
        except Exception:
            await event.edit("<b>Invalid user id.</b>", parse_mode="html")
            return
        self.app.security.add_sudo_user(user_id)
        await event.edit(f"<b>Sudo added:</b> <code>{user_id}</code>", parse_mode="html")

    @command("sudorm", description="Remove sudo user id", usage=".sudorm 123456789", security="owner")
    async def sudorm_cmd(self, event, args):
        if not args:
            await event.edit("<b>Usage:</b> <code>.sudorm user_id</code>", parse_mode="html")
            return
        try:
            user_id = int(args[0])
        except Exception:
            await event.edit("<b>Invalid user id.</b>", parse_mode="html")
            return
        self.app.security.remove_sudo_user(user_id)
        await event.edit(f"<b>Sudo removed:</b> <code>{user_id}</code>", parse_mode="html")

    @command("panelkey", description="Show panel password", usage=".panelkey", security="owner")
    async def panelkey_cmd(self, event, args):
        env_path = ROOT_DIR / ".env"
        if not env_path.exists():
            await event.edit("<b>.env not found.</b>", parse_mode="html")
            return
        key = ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("PANEL_PASSWORD="):
                key = line.split("=", 1)[1].strip()
                break
        if not key:
            await event.edit("<b>PANEL_PASSWORD is not set.</b>", parse_mode="html")
            return
        await event.edit(f"<b>Panel password:</b> <code>{html.escape(key)}</code>", parse_mode="html")

    @command("crebot1", description="Show or save inline bot token", usage=".crebot1 [token]", security="owner")
    async def crebot1_cmd(self, event, args):
        await self._crebot_flow(event, args, 1)

    @command("crebot2", description="Show or save helper bot token", usage=".crebot2 [token]", security="owner")
    async def crebot2_cmd(self, event, args):
        await self._crebot_flow(event, args, 2)

    @command("crebot3", description="Show or save community bot token", usage=".crebot3 [token]", security="owner")
    async def crebot3_cmd(self, event, args):
        await self._crebot_flow(event, args, 3)

    @command("dtgcheck", description="Check DeathTG integrity", usage=".dtgcheck", security="owner")
    async def dtgcheck_cmd(self, event, args):
        await event.edit("<b>Checking DeathTG integrity...</b>", parse_mode="html")
        status = await check_runtime_integrity(self.app.client, notify=False)
        report = html.escape(render_integrity_report(status))
        await event.edit(
            "<b>DeathTG integrity</b>\n<pre>" + report[:3600] + "</pre>",
            parse_mode="html",
        )

    @command("logs", description="Show recent DeathTG logs", usage=".logs [lines]", security="owner")
    async def logs_cmd(self, event, args):
        lines_to_show = 80
        if args:
            try:
                lines_to_show = max(20, min(int(args[0]), 400))
            except Exception:
                lines_to_show = 80
        log_path = RUNTIME_DIR / "deathtg.log"
        if not log_path.exists():
            await event.edit("<b>Log file is not created yet.</b>", parse_mode="html")
            return
        raw_lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(raw_lines[-lines_to_show:]).strip() or "log is empty"
        escaped = html.escape(tail)[-3600:]
        await event.edit(
            f"<b>DeathTG logs</b>\n<pre>{escaped}</pre>",
            parse_mode="html",
        )

    @command("tdgup", description="Update DeathTG using git pull", usage=".tdgup", aliases=("dtgup",), security="owner")
    async def tdgup_cmd(self, event, args):
        msg = await event.edit("<b>Updating DeathTG...</b>", parse_mode="html")
        try:
            result = await self._run_update_job()
            tail = html.escape(str(result.get("message") or "No output"))[-3000:]
            if not result.get("ok"):
                await msg.edit(f"<b>Update failed.</b>\n<pre>{tail}</pre>", parse_mode="html")
                return
            if result.get("updated"):
                await msg.edit(f"<b>DeathTG updated.</b>\n<pre>{tail}</pre>\nRun <code>.restart</code> to apply changes.", parse_mode="html")
                return
            await msg.edit(f"<b>Already up to date.</b>\n<pre>{tail}</pre>", parse_mode="html")
        except Exception as exc:
            await msg.edit(
                f"<b>Update failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>",
                parse_mode="html",
            )

    async def _run_update_job(self) -> dict[str, object]:
        import asyncio

        return await asyncio.to_thread(apply_update)

    async def _crebot_flow(self, event, args, slot: int) -> None:
        me = await self.app.client.get_me()
        owner_id = int(getattr(me, "id", 0) or 0)
        blueprint = manual_bot_blueprint(owner_id, slot)
        if not blueprint:
            await event.edit("<b>Unknown bot slot.</b>", parse_mode="html")
            return
        if not args:
            guide = html.escape(render_manual_bot_guide(owner_id, slot))
            await event.edit(f"<pre>{guide}</pre>", parse_mode="html")
            return
        token = args[0].strip()
        if not re.fullmatch(BOT_TOKEN_RE.pattern, token):
            await event.edit("<b>Invalid bot token format.</b>", parse_mode="html")
            return
        update_env_value(blueprint["env_key"], token)
        if blueprint["role"] == "community":
            update_env_value("COMMUNITY_BOT_USERNAME", blueprint["username"])
        await event.edit("<b>Token saved. Running DeathTG sync...</b>", parse_mode="html")
        status = await run_startup_sync(self.app.client)
        report = html.escape(render_integrity_report(status))
        await event.edit(
            "<b>Bot token saved.</b>\n<pre>" + report[:3400] + "</pre>",
            parse_mode="html",
        )

    @command("dlmod", description="Install module from DTG_Modules or raw URL", usage=".dlmod [module_name|raw_url]", security="owner")
    async def dlmod_cmd(self, event, args):
        if not args:
            items = await fetch_repo_modules()
            if not items:
                await event.edit("<b>DTG_Modules is unavailable right now.</b>", parse_mode="html")
                return
            await self.inline_send(
                event,
                self._dlmod_page_text(items, 0),
                reply_markup=self._dlmod_page_buttons(items, 0),
                parse_mode="html",
                link_preview=False,
                ttl=3600,
            )
            return
        msg = await event.edit("<b>Downloading module...</b>", parse_mode="html")
        try:
            requested = args[0].strip()
            module_ref = requested
            blob_hint = ""
            if is_url(requested):
                normalized = normalize_github_raw_url(requested)
                if normalized != requested and "/blob/" in requested:
                    blob_hint = "\n<i>Blob link auto-converted to raw.</i>"
                module_ref = normalized
            else:
                found = await find_repo_module(requested)
                if not found:
                    await msg.edit(
                        f"<b>Module not found in DTG_Modules:</b> <code>{html.escape(requested)}</code>",
                        parse_mode="html",
                    )
                    return
                module_ref = str(found.get("link") or "")
                if not module_ref:
                    await msg.edit("<b>Module exists but has no downloadable raw file.</b>", parse_mode="html")
                    return
            trusted = trusted_repo_link(module_ref) or _is_trusted_dtg_link(module_ref)
            path = await self.app.loader.download_module(module_ref, force=trusted)
            try:
                module_name = await self.app.loader.load_file(path, force=trusted)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            await msg.edit(
                f"<b>Module loaded:</b> <code>{html.escape(module_name)}</code>{blob_hint}",
                parse_mode="html",
            )
        except Exception as exc:
            await msg.edit(
                f"<b>dlmod failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>",
                parse_mode="html",
            )

    async def dlmod_page_callback(self, call, page: int = 0):
        items = await fetch_repo_modules()
        await call.edit(
            self._dlmod_page_text(items, page),
            reply_markup=self._dlmod_page_buttons(items, page),
            parse_mode="html",
            link_preview=False,
        )

    async def dlmod_install_callback(self, call, link: str, label: str):
        await call.edit(f"<b>Installing:</b> <code>{html.escape(label)}</code>", reply_markup=None, parse_mode="html")
        try:
            trusted = trusted_repo_link(link) or _is_trusted_dtg_link(link)
            path = await self.app.loader.download_module(link, force=trusted)
            try:
                module_name = await self.app.loader.load_file(path, force=trusted)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            await call.edit(
                f"<b>Module loaded:</b> <code>{html.escape(module_name)}</code>",
                reply_markup=None,
                parse_mode="html",
            )
        except Exception as exc:
            await call.edit(
                f"<b>dlmod failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>",
                reply_markup=None,
                parse_mode="html",
            )

    @command("loadmod", description="Load local module from modules folder", usage=".loadmod filename.py | ModuleFolder", security="owner")
    async def loadmod_cmd(self, event, args):
        if not args:
            await event.edit("<b>Usage:</b> <code>.loadmod example.py</code> or <code>.loadmod MyModule</code>", parse_mode="html")
            return
        try:
            path = MODULES_DIR / Path(args[0]).name
            if not path.exists():
                folder = MODULES_DIR / args[0].strip()
                if folder.exists():
                    path = folder
            module_name = await self.app.loader.load_file(path)
            await event.edit(f"<b>Module loaded:</b> <code>{html.escape(module_name)}</code>", parse_mode="html")
        except Exception as exc:
            await event.edit(
                f"<b>loadmod failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>",
                parse_mode="html",
            )

    @command("unloadmod", description="Unload module", usage=".unloadmod module_name", aliases=("unloadnod",), security="owner")
    async def unloadmod_cmd(self, event, args):
        if not args:
            await event.edit("<b>Provide module name.</b>", parse_mode="html")
            return
        module_name = args[0]
        try:
            removed = self.app.loader.unload(module_name)
            await event.edit(
                f"<b>Module unloaded:</b> <code>{html.escape(module_name)}</code>\nCommands removed: <code>{len(removed)}</code>",
                parse_mode="html",
            )
        except Exception as exc:
            await event.edit(
                f"<b>unloadmod failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>",
                parse_mode="html",
            )

    async def module_help_callback(self, call, module_name: str, page: int = 0):
        await call.edit(
            self._module_help_text(module_name),
            reply_markup=self.inline_buttons(
                [{"text": "Back", "callback": self.help_page_callback, "args": (page,)}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def help_page_callback(self, call, page: int = 0):
        await call.edit(
            self._helpb_index_text(page),
            reply_markup=self._helpb_index_buttons(page),
            parse_mode="html",
            link_preview=False,
        )

    async def close_callback(self, call):
        await call.edit("Closed.", reply_markup=None)

    async def _send_module_help(self, event, module_name: str, page: int = 0):
        await self.inline_send(
            event,
            self._module_help_text(module_name),
            reply_markup=self.inline_buttons(
                [{"text": "Back", "callback": self.help_page_callback, "args": (page,)}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    def _help_all_text(self) -> str:
        grouped = self.app.registry.by_module()
        command_count = sum(len(commands) for commands in grouped.values())
        module_count = len(grouped)
        lines = [
            "<b>DeathTG Commands</b>",
            f"Modules: <code>{module_count}</code> | Commands: <code>{command_count}</code>",
            "",
        ]
        for module, commands in sorted(grouped.items()):
            names = " ".join(_cmd(cmd.name) for cmd in sorted(commands, key=lambda item: item.name))
            lock = " [protected]" if module in PROTECTED_MODULES else ""
            lines.append(f"<b>{self._module_icon(module)} {html.escape(module)}{lock}</b>\n{names}")
        lines.extend(["", "Use <code>.help module</code> for details and <code>.helpb</code> for inline browser."])
        return "\n".join(lines)

    def _helpb_index_text(self, page: int) -> str:
        grouped = self.app.registry.by_module()
        modules = sorted(grouped)
        pages = self._page_count(modules)
        page = max(0, min(page, pages - 1))
        start = page * HELP_PAGE_SIZE
        chunk = modules[start:start + HELP_PAGE_SIZE]
        lines = [
            "<b>DeathTG Help Browser</b>",
            "Choose a module below.",
            "",
            f"Page <code>{page + 1}</code>/<code>{pages}</code>",
        ]
        for name in chunk:
            count = len(grouped.get(name, []))
            lock = "protected" if name in PROTECTED_MODULES else "external"
            lines.append(f"{self._module_icon(name)} <b>{html.escape(name)}</b> - <code>{count}</code> cmds ({lock})")
        return "\n".join(lines)

    def _dlmod_page_text(self, items: list[dict], page: int) -> str:
        total = len(items)
        pages = max(1, (total + DLMOD_PAGE_SIZE - 1) // DLMOD_PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        start = page * DLMOD_PAGE_SIZE
        chunk = items[start:start + DLMOD_PAGE_SIZE]
        lines = [
            "<b>DTG_Modules Browser</b>",
            "",
            f"Page <code>{page + 1}</code>/<code>{pages}</code>",
            "",
        ]
        for index, item in enumerate(chunk, start=1):
            name = str(item.get("name") or "module")
            description = str(item.get("description") or "DTG module")
            lines.append(f"<b>{index}. {html.escape(name)}</b>\n{html.escape(description)}")
        lines.extend(["", "Use buttons below to install a module from this page."])
        return "\n".join(lines)

    def _dlmod_page_buttons(self, items: list[dict], page: int):
        total = len(items)
        pages = max(1, (total + DLMOD_PAGE_SIZE - 1) // DLMOD_PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        start = page * DLMOD_PAGE_SIZE
        chunk = items[start:start + DLMOD_PAGE_SIZE]
        rows = []
        for item in chunk:
            name = str(item.get("name") or "module")
            link = str(item.get("link") or "")
            if not link:
                continue
            rows.append(
                [
                    {
                        "text": f"Install {name}"[:32],
                        "callback": self.dlmod_install_callback,
                        "args": (link, name),
                    }
                ]
            )
        nav = []
        if page > 0:
            nav.append({"text": "Back", "callback": self.dlmod_page_callback, "args": (page - 1,)})
        if start + DLMOD_PAGE_SIZE < total:
            nav.append({"text": "Next", "callback": self.dlmod_page_callback, "args": (page + 1,)})
        if nav:
            rows.append(nav)
        rows.append([{"text": "Close", "callback": self.close_callback, "args": ()}])
        return self.inline_buttons(*rows)

    def _modules_text(self) -> str:
        items = sorted(self.app.loader.loaded)
        if not items:
            return "<b>Loaded modules</b>\nNo modules loaded yet."
        lines = ["<b>Loaded modules</b>"]
        for name in items:
            marker = "protected" if name in PROTECTED_MODULES else "external"
            lines.append(f"{self._module_icon(name)} <code>{html.escape(name)}</code> <i>{marker}</i>")
        return "\n".join(lines)

    def _module_help_text(self, module_name: str) -> str:
        grouped = self.app.registry.by_module()
        real_name = self._find_module_name(module_name, grouped)
        commands = grouped.get(real_name)
        if not commands:
            return f"<b>Module not found:</b> <code>{html.escape(module_name)}</code>"
        marker = "protected" if real_name in PROTECTED_MODULES else "module"
        lines = [f"<b>{self._module_icon(real_name)} DeathTG / {html.escape(real_name)}</b> <i>{marker}</i>", ""]
        for cmd in sorted(commands, key=lambda item: item.name):
            usage = f"\n  <i>{html.escape(cmd.usage)}</i>" if cmd.usage else ""
            aliases = f" | aliases: <code>{html.escape(', '.join(cmd.aliases))}</code>" if cmd.aliases else ""
            security = ", ".join(sorted(parse_security(cmd.security)))
            lines.append(
                f"- {_cmd(cmd.name)} - {html.escape(cmd.description)}{aliases}\n"
                f"  security: <code>{html.escape(security)}</code>{usage}"
            )
        return "\n".join(lines)

    def _helpb_index_buttons(self, page: int):
        modules = sorted(self.app.registry.by_module())
        pages = self._page_count(modules)
        page = max(0, min(page, pages - 1))
        start = page * HELP_PAGE_SIZE
        chunk = modules[start:start + HELP_PAGE_SIZE]
        rows = []
        for idx in range(0, len(chunk), 2):
            row = []
            for name in chunk[idx:idx + 2]:
                row.append(
                    {
                        "text": f"{self._module_icon(name)} {name}"[:32],
                        "callback": self.module_help_callback,
                        "args": (name, page),
                    }
                )
            rows.append(row)
        nav = []
        if page > 0:
            nav.append({"text": "Back", "callback": self.help_page_callback, "args": (page - 1,)})
        if page + 1 < pages:
            nav.append({"text": "Next", "callback": self.help_page_callback, "args": (page + 1,)})
        if nav:
            rows.append(nav)
        rows.append([{"text": "Close", "callback": self.close_callback, "args": ()}])
        return self.inline_buttons(*rows)

    @staticmethod
    def _page_count(items: list[str]) -> int:
        return max(1, (len(items) + HELP_PAGE_SIZE - 1) // HELP_PAGE_SIZE)

    @staticmethod
    def _find_module_name(module_name: str, grouped: dict[str, list]) -> str:
        needle = module_name.strip().lower()
        for name in grouped:
            if name.lower() == needle:
                return name
        return module_name

    @staticmethod
    def _module_icon(name: str) -> str:
        lowered = name.lower()
        if lowered in {"core", "help"}:
            return "🧠"
        if lowered == "root":
            return "🗂"
        if lowered == "info":
            return "🧾"
        if lowered == "system":
            return "⚡"
        if lowered == "antivirus":
            return "🛡"
        if lowered == "terminal":
            return "🖥"
        if "music" in lowered or "track" in lowered:
            return "🎧"
        return "📦"
