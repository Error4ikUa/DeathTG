from __future__ import annotations

import asyncio
import html
import shlex

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

BLOCKED_PARTS = {
    "rm",
    "del",
    "format",
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    "dd",
    "curl",
    "wget",
    "nc",
    "netcat",
    "chmod",
    "chown",
    "sudo",
    "su",
    "git",
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
        for part in parts:
            if part.lower() in BLOCKED_PARTS:
                return False, f"Token '{part}' is blocked."
        return True, ""

    async def _run(self, raw: str) -> str:
        proc = await asyncio.create_subprocess_shell(
            raw,
            cwd=str(ROOT_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            return "Command timed out."
        return (stdout + stderr).decode("utf-8", errors="replace").strip()[-3000:] or "Empty output."

    @staticmethod
    def _result_text(raw: str, output: str) -> str:
        return (
            "<b>Terminal result</b>\n"
            f"Command: <code>{html.escape(raw)}</code>\n"
            f"<pre>{html.escape(output)}</pre>"
        )
