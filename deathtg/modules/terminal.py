from __future__ import annotations

import datetime as dt
import getpass
import html
import platform
import shlex
import shutil
import sys

import psutil

from deathtg.command import command
from deathtg.config import ROOT_DIR
from deathtg.loader import Module

SAFE_COMMANDS = {
    "pwd",
    "ls",
    "dir",
    "whoami",
    "uname",
    "date",
    "uptime",
    "df",
    "free",
    "python",
    "python3",
}

SAFE_ARGUMENTS = {
    "pwd": {()},
    "ls": {(), ("-l",), ("-la",), ("-al",)},
    "dir": {()},
    "whoami": {()},
    "uname": {(), ("-a",)},
    "date": {()},
    "uptime": {()},
    "df": {(), ("-h",)},
    "free": {(), ("-h",), ("-m",)},
    "python": {("--version",), ("-V",)},
    "python3": {("--version",), ("-V",)},
}


class TerminalMod(Module):
    strings = {"name": "terminal"}

    @command("term", description="Run a safe terminal command", usage=".term ls")
    async def term_cmd(self, event, args):
        raw = " ".join(args).strip()
        if not raw:
            await self.inline_send(
                event,
                "<b>Terminal</b>\nChoose a preset or run <code>.term ls</code>.",
                reply_markup=self._preset_buttons(),
                parse_mode="html",
                link_preview=False,
                ttl=3600,
            )
            return

        allowed, reason = self._is_safe(raw)
        if not allowed:
            await self.inline_send(
                event,
                f"<b>Terminal blocked</b>\n<code>{html.escape(reason)}</code>",
                reply_markup=self._preset_buttons(),
                parse_mode="html",
                link_preview=False,
                ttl=3600,
            )
            return

        output = await self._run(raw)
        await self.inline_send(
            event,
            self._result_text(raw, output),
            reply_markup=self.inline_buttons(
                [{"text": "Repeat", "callback": self.run_callback, "args": (raw,)}],
                [{"text": "Presets", "callback": self.presets_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def presets_callback(self, call):
        await call.edit(
            "<b>Terminal</b>\nChoose a safe preset.",
            reply_markup=self._preset_buttons(),
            parse_mode="html",
            link_preview=False,
        )

    async def run_callback(self, call, raw: str):
        allowed, reason = self._is_safe(raw)
        if not allowed:
            await call.edit(
                f"<b>Terminal blocked</b>\n<code>{html.escape(reason)}</code>",
                reply_markup=self._preset_buttons(),
                parse_mode="html",
                link_preview=False,
            )
            return
        await call.edit("<b>Running...</b>", reply_markup=None, parse_mode="html")
        output = await self._run(raw)
        await call.edit(
            self._result_text(raw, output),
            reply_markup=self.inline_buttons(
                [{"text": "Repeat", "callback": self.run_callback, "args": (raw,)}],
                [{"text": "Presets", "callback": self.presets_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def close_callback(self, call):
        await call.edit("Closed.", reply_markup=None)

    def _preset_buttons(self):
        return self.inline_buttons(
            [{"text": "pwd", "callback": self.run_callback, "args": ("pwd",)}],
            [{"text": "ls", "callback": self.run_callback, "args": ("ls",)}],
            [{"text": "whoami", "callback": self.run_callback, "args": ("whoami",)}],
            [{"text": "python --version", "callback": self.run_callback, "args": ("python --version",)}],
            [{"text": "Close", "callback": self.close_callback, "args": ()}],
        )

    def _is_safe(self, raw: str) -> tuple[bool, str]:
        try:
            parts = shlex.split(raw)
        except Exception:
            return False, "Failed to parse command."
        if not parts:
            return False, "Empty command."
        base = parts[0].lower()
        if base not in SAFE_COMMANDS:
            return False, f"Command '{base}' is not allowed."
        arguments = tuple(parts[1:])
        if arguments not in SAFE_ARGUMENTS[base]:
            return False, f"Arguments for '{base}' are not allowed. Use a preset."
        return True, ""

    async def _run(self, raw: str) -> str:
        parts = shlex.split(raw)
        command = parts[0].lower()
        if command == "pwd":
            return str(ROOT_DIR)
        if command in {"ls", "dir"}:
            rows = []
            for path in sorted(ROOT_DIR.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                kind = "d" if path.is_dir() else "f"
                size = "-" if path.is_dir() else str(path.stat().st_size)
                rows.append(f"{kind} {size:>10} {path.name}")
            return "\n".join(rows)[-3000:] or "Empty directory."
        if command == "whoami":
            return getpass.getuser()
        if command == "uname":
            return platform.platform()
        if command == "date":
            return dt.datetime.now().astimezone().isoformat(timespec="seconds")
        if command == "uptime":
            seconds = max(0, int(dt.datetime.now().timestamp() - psutil.boot_time()))
            days, remainder = divmod(seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
        if command == "df":
            usage = shutil.disk_usage(ROOT_DIR)
            return f"total={usage.total} used={usage.used} free={usage.free}"
        if command == "free":
            memory = psutil.virtual_memory()
            return f"total={memory.total} used={memory.used} available={memory.available} percent={memory.percent}%"
        if command in {"python", "python3"}:
            return sys.version.replace("\n", " ")
        return "Unsupported diagnostic."

    @staticmethod
    def _result_text(raw: str, output: str) -> str:
        return (
            "<b>Terminal result</b>\n"
            f"Command: <code>{html.escape(raw)}</code>\n"
            f"<pre>{html.escape(output)}</pre>"
        )
