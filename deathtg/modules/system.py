from __future__ import annotations

import platform
import time

from deathtg import __version__
from deathtg.command import command
from deathtg.ui import box, ok

STARTED_AT = time.time()


@command("ping", description="Проверить задержку бота", usage=".ping")
async def ping_cmd(event, args: list[str]) -> None:
    start = time.perf_counter()
    msg = await event.edit("<b>☠️ DeathTG:</b> ping...", parse_mode="html")
    latency = (time.perf_counter() - start) * 1000
    await msg.edit(ok(f"pong <code>{latency:.1f} ms</code>"), parse_mode="html")


@command("alive", description="Показать статус DeathTG", usage=".alive")
async def alive_cmd(event, args: list[str]) -> None:
    uptime = int(time.time() - STARTED_AT)
    hours, rem = divmod(uptime, 3600)
    minutes, seconds = divmod(rem, 60)
    lines = [
        f"Version: <code>{__version__}</code>",
        f"Python: <code>{platform.python_version()}</code>",
        f"OS: <code>{platform.system()} {platform.release()}</code>",
        f"Uptime: <code>{hours}h {minutes}m {seconds}s</code>",
    ]
    await event.edit(box("DeathTG alive", lines), parse_mode="html")
