from __future__ import annotations

import time

from deathtg.command import command
from deathtg.config import load_config
from deathtg.metrics import installed_days, usage_total, top_modules
from deathtg.profile_store import profile_settings


def _bar(value: int, maximum: int = 100, size: int = 12) -> str:
    maximum = max(1, maximum)
    filled = max(0, min(size, round((value / maximum) * size)))
    return "█" * filled + "░" * (size - filled)


@command("info", description="Show DeathTG profile, uptime, ping and status", usage=".info", aliases=("me", "profile"))
async def info_cmd(event, args: list[str]) -> None:
    started = time.perf_counter()
    msg = await event.edit("<b>☠️ DeathTG:</b> checking status...", parse_mode="html")
    ping = int((time.perf_counter() - started) * 1000)
    me = await event.client.get_me()
    cfg = load_config()
    settings = profile_settings()
    total = usage_total()
    level = total // 100 + 1
    progress = total % 100
    modules = top_modules(3)
    module_line = ", ".join(f"{m['module']}({m['count']})" for m in modules) or "no usage yet"
    name = " ".join([me.first_name or "", me.last_name or ""]).strip() or me.username or "DeathTG User"
    username = f"@{me.username}" if me.username else "no username"
    description = settings.get("description") or "DeathTG userbot online."
    text = (
        "<b>☠️ DeathTG Operator</b>\n"
        f"<b>{name}</b> · <code>{username}</code>\n"
        f"<i>{description}</i>\n\n"
        f"<b>Status:</b> online\n"
        f"<b>Ping:</b> <code>{ping} ms</code>\n"
        f"<b>Prefix:</b> <code>{cfg.command_prefix}</code>\n"
        f"<b>Streak:</b> <code>{installed_days()} days</code>\n"
        f"<b>Actions:</b> <code>{total}</code>\n"
        f"<b>Level:</b> <code>{level}</code> [{_bar(progress)}] {progress}/100\n"
        f"<b>Top modules:</b> <code>{module_line}</code>"
    )
    await msg.edit(text, parse_mode="html")
