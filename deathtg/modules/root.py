from __future__ import annotations

import asyncio
import html
import shlex
import subprocess

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


class RootMod(Module):
    strings = {"name": "root"}

    @command("root", description="Root inline panel", usage=".root")
    async def root_cmd(self, event, args):
        await self._show_root_menu(event)

    @command("update", description="Update DeathTG (inline)", usage=".update")
    async def update_cmd(self, event, args):
        await self.inline_send(
            event,
            "<b>🔄 System update</b>\nRun <code>git pull --ff-only</code> in DeathTG root?",
            reply_markup=self.inline_buttons(
                [{"text": "🚀 Run Update", "callback": self.run_update_callback, "args": ()}],
                [{"text": "⬅️ Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    @command("terminal", description="Terminal command (inline)", usage=".terminal <command>")
    async def terminal_cmd(self, event, args):
        raw = " ".join(args).strip()
        if not raw:
            await self.inline_send(
                event,
                "<b>🖥️ Terminal</b>\nUse: <code>.terminal ls</code>",
                reply_markup=self.inline_buttons(
                    [{"text": "📍 pwd", "callback": self.run_term_callback, "args": ("pwd",)}],
                    [{"text": "📁 ls", "callback": self.run_term_callback, "args": ("ls",)}],
                    [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
                ),
                parse_mode="html",
                link_preview=False,
                ttl=3600,
            )
            return

        allowed, reason = self._is_safe_terminal_command(raw)
        if not allowed:
            await event.edit(f"<b>Terminal blocked:</b>\n<code>{html.escape(reason)}</code>", parse_mode="html")
            return

        output = await self._run_shell_command(raw)
        text = (
            "<b>🖥️ Terminal result</b>\n"
            f"Command: <code>{html.escape(raw)}</code>\n"
            f"<pre>{html.escape(output)}</pre>"
        )
        await self.inline_send(
            event,
            text,
            reply_markup=self.inline_buttons(
                [{"text": "⬅️ Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def _show_root_menu(self, event):
        await self.inline_send(
            event,
            "<b>👑 DeathTG Root</b>\nInline control panel for update, terminal and help.",
            reply_markup=self.inline_buttons(
                [{"text": "🔄 Update System", "callback": self.update_menu_callback, "args": ()}],
                [{"text": "🖥️ Terminal Presets", "callback": self.terminal_menu_callback, "args": ()}],
                [{"text": "🦇 Help Browser", "callback": self.help_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def update_menu_callback(self, call):
        await call.edit(
            "<b>🔄 System update</b>\nRun <code>git pull --ff-only</code> in DeathTG root?",
            reply_markup=self.inline_buttons(
                [{"text": "🚀 Run Update", "callback": self.run_update_callback, "args": ()}],
                [{"text": "⬅️ Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def terminal_menu_callback(self, call):
        await call.edit(
            "<b>🖥️ Terminal presets</b>\nChoose a safe command.",
            reply_markup=self.inline_buttons(
                [{"text": "📍 pwd", "callback": self.run_term_callback, "args": ("pwd",)}],
                [{"text": "📁 ls", "callback": self.run_term_callback, "args": ("ls",)}],
                [{"text": "👤 whoami", "callback": self.run_term_callback, "args": ("whoami",)}],
                [{"text": "🐍 python --version", "callback": self.run_term_callback, "args": ("python --version",)}],
                [{"text": "⬅️ Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def back_root_callback(self, call):
        await call.edit(
            "<b>👑 DeathTG Root</b>\nInline control panel for update, terminal and help.",
            reply_markup=self.inline_buttons(
                [{"text": "🔄 Update System", "callback": self.update_menu_callback, "args": ()}],
                [{"text": "🖥️ Terminal Presets", "callback": self.terminal_menu_callback, "args": ()}],
                [{"text": "🦇 Help Browser", "callback": self.help_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def run_update_callback(self, call):
        await call.edit("<b>🔄 Updating...</b>", reply_markup=None, parse_mode="html")
        text = await asyncio.to_thread(self._run_git_pull)
        await call.edit(
            text,
            reply_markup=self.inline_buttons(
                [{"text": "⬅️ Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def run_term_callback(self, call, command_text: str):
        allowed, reason = self._is_safe_terminal_command(command_text)
        if not allowed:
            await call.edit(
                f"<b>🛡️ Terminal blocked:</b>\n<code>{html.escape(reason)}</code>",
                reply_markup=self.inline_buttons(
                    [{"text": "⬅️ Back", "callback": self.terminal_menu_callback, "args": ()}],
                    [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
                ),
                parse_mode="html",
                link_preview=False,
            )
            return

        await call.edit("<b>🖥️ Running terminal command...</b>", reply_markup=None, parse_mode="html")
        output = await self._run_shell_command(command_text)
        await call.edit(
            (
                "<b>🖥️ Terminal result</b>\n"
                f"Command: <code>{html.escape(command_text)}</code>\n"
                f"<pre>{html.escape(output)}</pre>"
            ),
            reply_markup=self.inline_buttons(
                [{"text": "⬅️ Back", "callback": self.terminal_menu_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def help_callback(self, call):
        await call.edit(
            (
                "<b>🦇 DeathTG Help</b>\n"
                "Use <code>.help</code> for compact text help.\n"
                "Use <code>.helpb</code> for the button help browser."
            ),
            reply_markup=self.inline_buttons(
                [{"text": "⬅️ Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "✖️ Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def close_callback(self, call):
        await call.edit("✨ Closed.", reply_markup=None)

    def _run_git_pull(self) -> str:
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                timeout=120,
            )
            out = (result.stdout + "\n" + result.stderr).strip() or "No output."
            tail = out[-3000:]
            if result.returncode != 0:
                return f"<b>Update failed.</b>\n<pre>{html.escape(tail)}</pre>"
            return (
                "<b>Update completed.</b>\n"
                f"<pre>{html.escape(tail)}</pre>\n"
                "Restart DeathTG to apply all changes."
            )
        except Exception as exc:
            return f"<b>Update failed:</b>\n<code>{html.escape(type(exc).__name__ + ': ' + str(exc))}</code>"

    def _is_safe_terminal_command(self, raw: str) -> tuple[bool, str]:
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

    async def _run_shell_command(self, raw: str) -> str:
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
        out = (stdout + stderr).decode("utf-8", errors="replace").strip() or "Empty output."
        return out[-3000:]
