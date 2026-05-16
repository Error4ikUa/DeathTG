from __future__ import annotations

import html
from pathlib import Path

from deathtg.command import command
from deathtg.config import MODULES_DIR
from deathtg.loader import Module
from deathtg.security import scan_module_source


class AntivirusMod(Module):
    strings = {"name": "antivirus"}

    @command("antivirus", description="Show module security status", usage=".antivirus")
    async def antivirus_cmd(self, event, args):
        await self.inline_send(
            event,
            self._status_text(),
            reply_markup=self.inline_buttons(
                [{"text": "Scan Modules", "callback": self.scan_all_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    @command("scanmod", description="Scan a local module with DeathTG security", usage=".scanmod module.py")
    async def scanmod_cmd(self, event, args):
        if not args:
            await self.inline_send(
                event,
                "<b>DeathTG Antivirus</b>\nUse: <code>.scanmod module.py</code>",
                reply_markup=self.inline_buttons(
                    [{"text": "Scan Modules", "callback": self.scan_all_callback, "args": ()}],
                    [{"text": "Close", "callback": self.close_callback, "args": ()}],
                ),
                parse_mode="html",
                link_preview=False,
                ttl=3600,
            )
            return

        path = MODULES_DIR / Path(args[0]).name
        text = self._scan_file_text(path)
        await self.inline_send(
            event,
            text,
            reply_markup=self.inline_buttons(
                [{"text": "Scan Modules", "callback": self.scan_all_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def scan_all_callback(self, call):
        rows = []
        for path in sorted(MODULES_DIR.glob("*.py"))[:12]:
            rows.append([{"text": path.name[:32], "callback": self.scan_one_callback, "args": (path.name,)}])
        rows.append([{"text": "Back", "callback": self.status_callback, "args": ()}])
        rows.append([{"text": "Close", "callback": self.close_callback, "args": ()}])
        await call.edit(
            "<b>DeathTG Antivirus</b>\nChoose a module to scan.",
            reply_markup=self.inline_buttons(*rows),
            parse_mode="html",
            link_preview=False,
        )

    async def scan_one_callback(self, call, filename: str):
        path = MODULES_DIR / Path(filename).name
        await call.edit(
            self._scan_file_text(path),
            reply_markup=self.inline_buttons(
                [{"text": "Back", "callback": self.scan_all_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def status_callback(self, call):
        await call.edit(
            self._status_text(),
            reply_markup=self.inline_buttons(
                [{"text": "Scan Modules", "callback": self.scan_all_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def close_callback(self, call):
        await call.edit("Closed.", reply_markup=None)

    @staticmethod
    def _status_text() -> str:
        return (
            "<b>DeathTG Antivirus</b>\n"
            "Status: <code>enabled</code>\n"
            "Checks: <code>account deletion, logout, sessions, eval/exec, shell, destructive files</code>\n"
            "Protected modules: <code>core, root, info, system, antivirus, terminal</code>"
        )

    @staticmethod
    def _scan_file_text(path: Path) -> str:
        if not path.exists():
            return f"<b>DeathTG Antivirus</b>\nFile <code>{html.escape(path.name)}</code> was not found."
        report = scan_module_source(path.read_text(encoding="utf-8"))
        verdict = "allowed" if report.allowed else "blocked"
        return (
            "<b>DeathTG Antivirus</b>\n"
            f"File: <code>{html.escape(path.name)}</code>\n"
            f"Verdict: <b>{verdict}</b>\n"
            f"Severity: <code>{html.escape(str(report.severity))}</code>\n"
            f"Score: <code>{html.escape(str(report.score))}</code>\n"
            f"<pre>{html.escape(report.pretty())}</pre>"
        )
