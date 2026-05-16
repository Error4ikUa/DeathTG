from __future__ import annotations

import html
import subprocess
from pathlib import Path

from deathtg.command import command
from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.loader import Module
from deathtg.permissions import parse_security
from deathtg.registry import PROTECTED_MODULES


HELP_PAGE_SIZE = 8


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

    @command("tdgup", description="Update DeathTG using git pull", usage=".tdgup", aliases=("dtgup",), security="owner")
    async def tdgup_cmd(self, event, args):
        msg = await event.edit("<b>Updating DeathTG...</b>", parse_mode="html")
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                timeout=120,
            )
            output = (result.stdout + "\n" + result.stderr).strip() or "Already up to date."
            tail = output[-3000:]
            if result.returncode != 0:
                await msg.edit(f"<b>Update failed.</b>\n<pre>{html.escape(tail)}</pre>", parse_mode="html")
                return
            await msg.edit(
                f"<b>DeathTG updated.</b>\n<pre>{html.escape(tail)}</pre>\nRestart DeathTG to apply changes.",
                parse_mode="html",
            )
        except Exception as exc:
            await msg.edit(
                f"<b>Update failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>",
                parse_mode="html",
            )

    @command("dlmod", description="Download and load module by URL", usage=".dlmod https://.../module.py", security="owner")
    async def dlmod_cmd(self, event, args):
        if not args:
            await event.edit("<b>Provide a .py module URL.</b>", parse_mode="html")
            return
        msg = await event.edit("<b>Downloading module...</b>", parse_mode="html")
        try:
            trusted = _is_trusted_dtg_link(args[0])
            path = await self.app.loader.download_module(args[0], force=trusted)
            try:
                module_name = await self.app.loader.load_file(path, force=trusted)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            await msg.edit(f"<b>Module loaded:</b> <code>{html.escape(module_name)}</code>", parse_mode="html")
        except Exception as exc:
            await msg.edit(
                f"<b>dlmod failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>",
                parse_mode="html",
            )

    @command("loadmod", description="Load local module from modules folder", usage=".loadmod filename.py", security="owner")
    async def loadmod_cmd(self, event, args):
        if not args:
            await event.edit("<b>Usage:</b> <code>.loadmod example.py</code>", parse_mode="html")
            return
        try:
            path = MODULES_DIR / Path(args[0]).name
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
