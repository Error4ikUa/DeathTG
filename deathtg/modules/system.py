from __future__ import annotations

import platform
import time

from deathtg import __version__
from deathtg.command import command
from deathtg.loader import Module

STARTED_AT = time.time()


class SystemMod(Module):
    strings = {"name": "system"}

    @command("ping", description="Check DeathTG latency", usage=".ping")
    async def ping_cmd(self, event, args):
        start = time.perf_counter()
        latency = (time.perf_counter() - start) * 1000
        await self.inline_send(
            event,
            f"<b>DeathTG ping</b>\nLatency: <code>{latency:.1f} ms</code>",
            reply_markup=self.inline_buttons(
                [{"text": "Refresh", "callback": self.ping_callback, "args": ()}],
                [{"text": "Alive", "callback": self.alive_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    @command("alive", description="Show DeathTG status", usage=".alive")
    async def alive_cmd(self, event, args):
        await self.inline_send(
            event,
            self._alive_text(),
            reply_markup=self._alive_buttons(),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def ping_callback(self, call):
        start = time.perf_counter()
        latency = (time.perf_counter() - start) * 1000
        await call.edit(
            f"<b>DeathTG ping</b>\nLatency: <code>{latency:.1f} ms</code>",
            reply_markup=self.inline_buttons(
                [{"text": "Refresh", "callback": self.ping_callback, "args": ()}],
                [{"text": "Alive", "callback": self.alive_callback, "args": ()}],
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
        )

    async def alive_callback(self, call):
        await call.edit(
            self._alive_text(),
            reply_markup=self._alive_buttons(),
            parse_mode="html",
            link_preview=False,
        )

    async def close_callback(self, call):
        await call.edit("Closed.", reply_markup=None)

    def _alive_text(self) -> str:
        uptime = int(time.time() - STARTED_AT)
        hours, rem = divmod(uptime, 3600)
        minutes, seconds = divmod(rem, 60)
        return (
            "<b>DeathTG alive</b>\n"
            f"Version: <code>{__version__}</code>\n"
            f"Python: <code>{platform.python_version()}</code>\n"
            f"OS: <code>{platform.system()} {platform.release()}</code>\n"
            f"Uptime: <code>{hours}h {minutes}m {seconds}s</code>"
        )

    def _alive_buttons(self):
        return self.inline_buttons(
            [{"text": "Ping", "callback": self.ping_callback, "args": ()}],
            [{"text": "Refresh", "callback": self.alive_callback, "args": ()}],
            [{"text": "Close", "callback": self.close_callback, "args": ()}],
        )
