from __future__ import annotations

import asyncio
import html
import shlex
import subprocess

from deathtg.command import command
from deathtg.config import ROOT_DIR
from deathtg.loader import Module
from deathtg.update_manager import apply_update, inspect_update, schedule_restart


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

    @command("update", description="Check and apply DeathTG update", usage=".update")
    async def update_cmd(self, event, args):
        info = await asyncio.to_thread(inspect_update)
        await self.inline_send(
            event,
            self._update_prompt_text(info),
            reply_markup=self.inline_buttons(*self._update_prompt_buttons(info)),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    @command("restart", description="Restart DeathTG", usage=".restart")
    async def restart_cmd(self, event, args):
        await self.inline_send(
            event,
            "<b>Restart DeathTG</b>\nRestart the full stack now?",
            reply_markup=self.inline_buttons(
                [{"text": "Restart Now", "callback": self.restart_callback, "args": ()}],
                [{"text": "Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
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
                "<b>Terminal</b>\nUse: <code>.terminal ls</code>",
                reply_markup=self.inline_buttons(
                    [{"text": "pwd", "callback": self.run_term_callback, "args": ("pwd",)}],
                    [{"text": "ls", "callback": self.run_term_callback, "args": ("ls",)}],
                    [{"text": "Close", "callback": self.close_callback, "args": ()}],
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
            "<b>Terminal result</b>\n"
            f"Command: <code>{html.escape(raw)}</code>\n"
            f"<pre>{html.escape(output)}</pre>"
        )
        await self.inline_send(
            event,
            text,
            reply_markup=self.inline_buttons(
                [{"text": "Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def _show_root_menu(self, event):
        await self.inline_send(
            event,
            "<b>DeathTG Root</b>\nInline control panel for update, restart, terminal and help.",
            reply_markup=self.inline_buttons(
                [{"text": "Update System", "callback": self.update_menu_callback, "args": ()}],
                [{"text": "Restart", "callback": self.restart_callback, "args": ()}],
                [{"text": "Terminal Presets", "callback": self.terminal_menu_callback, "args": ()}],
                [{"text": "Help Browser", "callback": self.help_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def update_menu_callback(self, call):
        info = await asyncio.to_thread(inspect_update)
        await call.edit(
            self._update_prompt_text(info),
            reply_markup=self.inline_buttons(*self._update_prompt_buttons(info)),
            parse_mode="html",
            link_preview=False,
        )

    async def terminal_menu_callback(self, call):
        await call.edit(
            "<b>Terminal presets</b>\nChoose a safe command.",
            reply_markup=self.inline_buttons(
                [{"text": "pwd", "callback": self.run_term_callback, "args": ("pwd",)}],
                [{"text": "ls", "callback": self.run_term_callback, "args": ("ls",)}],
                [{"text": "whoami", "callback": self.run_term_callback, "args": ("whoami",)}],
                [{"text": "python --version", "callback": self.run_term_callback, "args": ("python --version",)}],
                [{"text": "Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def back_root_callback(self, call):
        await call.edit(
            "<b>DeathTG Root</b>\nInline control panel for update, restart, terminal and help.",
            reply_markup=self.inline_buttons(
                [{"text": "Update System", "callback": self.update_menu_callback, "args": ()}],
                [{"text": "Restart", "callback": self.restart_callback, "args": ()}],
                [{"text": "Terminal Presets", "callback": self.terminal_menu_callback, "args": ()}],
                [{"text": "Help Browser", "callback": self.help_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def run_update_callback(self, call):
        await call.edit("<b>Updating...</b>", reply_markup=None, parse_mode="html")
        result = await asyncio.to_thread(apply_update)
        await call.edit(
            self._update_result_text(result),
            reply_markup=self.inline_buttons(*self._update_result_buttons(result)),
            parse_mode="html",
            link_preview=False,
        )

    async def restart_callback(self, call):
        schedule_restart()
        await call.edit(
            "<b>Restart scheduled.</b>\nDeathTG is restarting now. Reopen the panel or chat in a few seconds.",
            reply_markup=None,
            parse_mode="html",
            link_preview=False,
        )

    async def run_term_callback(self, call, command_text: str):
        allowed, reason = self._is_safe_terminal_command(command_text)
        if not allowed:
            await call.edit(
                f"<b>Terminal blocked:</b>\n<code>{html.escape(reason)}</code>",
                reply_markup=self.inline_buttons(
                    [{"text": "Back", "callback": self.terminal_menu_callback, "args": ()}],
                    [{"text": "Close", "callback": self.close_callback, "args": ()}],
                ),
                parse_mode="html",
                link_preview=False,
            )
            return

        await call.edit("<b>Running terminal command...</b>", reply_markup=None, parse_mode="html")
        output = await self._run_shell_command(command_text)
        await call.edit(
            (
                "<b>Terminal result</b>\n"
                f"Command: <code>{html.escape(command_text)}</code>\n"
                f"<pre>{html.escape(output)}</pre>"
            ),
            reply_markup=self.inline_buttons(
                [{"text": "Back", "callback": self.terminal_menu_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def help_callback(self, call):
        await call.edit(
            (
                "<b>DeathTG Help</b>\n"
                "Use <code>.help</code> for compact text help.\n"
                "Use <code>.helpb</code> for the button help browser."
            ),
            reply_markup=self.inline_buttons(
                [{"text": "Back", "callback": self.back_root_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def close_callback(self, call):
        await call.edit("Closed.", reply_markup=None)

    def _update_prompt_text(self, info: dict[str, object]) -> str:
        if not info.get("ok"):
            return f"<b>Update check failed.</b>\n<pre>{html.escape(str(info.get('message') or 'No details'))}</pre>"
        current = str(info.get("current") or "")[:10]
        upcoming = str(info.get("upcoming") or "")[:10]
        if info.get("update_available"):
            return (
                "<b>Update available</b>\n"
                f"Branch: <code>{html.escape(str(info.get('branch') or 'main'))}</code>\n"
                f"Current: <code>{html.escape(current)}</code>\n"
                f"Remote: <code>{html.escape(upcoming)}</code>\n"
                f"Behind: <code>{html.escape(str(info.get('behind') or 0))}</code>\n"
                "Apply git update now?"
            )
        return (
            "<b>Already up to date</b>\n"
            f"Branch: <code>{html.escape(str(info.get('branch') or 'main'))}</code>\n"
            f"Commit: <code>{html.escape(current or upcoming or 'unknown')}</code>"
        )

    def _update_prompt_buttons(self, info: dict[str, object]) -> list[list[dict]]:
        rows: list[list[dict]] = []
        if info.get("ok") and info.get("update_available"):
            rows.append([{"text": "Apply Update", "callback": self.run_update_callback, "args": ()}])
        rows.append([{"text": "Restart", "callback": self.restart_callback, "args": ()}])
        rows.append([{"text": "Back", "callback": self.back_root_callback, "args": ()}])
        rows.append([{"text": "Close", "callback": self.close_callback, "args": ()}])
        return rows

    def _update_result_text(self, result: dict[str, object]) -> str:
        message = html.escape(str(result.get("message") or "No output"))[-3000:]
        if not result.get("ok"):
            return f"<b>Update failed.</b>\n<pre>{message}</pre>"
        if result.get("updated"):
            return f"<b>Update installed.</b>\n<pre>{message}</pre>\nRestart DeathTG to apply changes."
        return f"<b>No update required.</b>\n<pre>{message}</pre>"

    def _update_result_buttons(self, result: dict[str, object]) -> list[list[dict]]:
        rows: list[list[dict]] = []
        if result.get("restart_required"):
            rows.append([{"text": "Restart Now", "callback": self.restart_callback, "args": ()}])
        rows.append([{"text": "Back", "callback": self.back_root_callback, "args": ()}])
        rows.append([{"text": "Close", "callback": self.close_callback, "args": ()}])
        return rows

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
        def _runner() -> str:
            try:
                result = subprocess.run(
                    raw,
                    cwd=ROOT_DIR,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=30,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                if not output:
                    output = "Command finished with no output."
                return output[-3500:]
            except Exception as exc:
                return f"{type(exc).__name__}: {exc}"

        return await asyncio.to_thread(_runner)
