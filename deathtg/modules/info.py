from __future__ import annotations

import contextlib
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from telethon import Button

from deathtg.command import command
from deathtg.config import RUNTIME_DIR, load_config
from deathtg.info_card import render_info_card
from deathtg.metrics import installed_days, level_info, top_modules, usage_by_day, usage_total
from deathtg.profile_store import profile_settings
from deathtg.startup_sync import STATUS_PATH


def _startup_status() -> dict:
    if STATUS_PATH.exists():
        try:
            return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "bot": {"configured": False, "username": "", "valid_username": False, "error": None},
        "channels": [],
        "folder": {"name": "DeathTG", "ok": False, "error": None},
    }


def _top_modules_text(rows: list[dict[str, object]]) -> str:
    parts = []
    for row in rows[:4]:
        name = str(row.get("module") or "unknown")
        count = int(row.get("count") or 0)
        parts.append(f"{name}({count})")
    return ", ".join(parts) or "No stats yet"


def _usage_chart_points(rows: list[dict[str, object]]) -> list[dict]:
    grouped: dict[str, int] = {}
    for row in rows:
        day = str(row.get("day") or "")
        grouped[day] = grouped.get(day, 0) + int(row.get("count") or 0)
    today = datetime.now().date()
    days = [(today - timedelta(days=idx)).isoformat() for idx in range(29, -1, -1)]
    return [{"day": day, "count": grouped.get(day, 0)} for day in days]


class _InfoFormatMap(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _git_identity() -> tuple[str, str]:
    try:
        version = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True, timeout=4).strip()
    except Exception:
        version = "local"
    try:
        branch = subprocess.check_output(["git", "branch", "--show-current"], text=True, timeout=4).strip()
    except Exception:
        branch = "main"
    return version, branch


def _info_caption(settings: dict[str, str], payload: dict[str, object]) -> str:
    template = (settings.get("info_text") or "").strip()
    if template:
        return template.format_map(_InfoFormatMap(payload))
    return (
        f"<blockquote><b>⬛️ {payload['title']}</b>\n"
        f"⌚️ {payload['username']}\n"
        f"🏴‍☠️ {payload['role']}\n"
        f"💻 <b>Prefix</b>: <code>{payload['prefix']}</code>\n"
        f"⌛️ <b>Level</b>: <code>{payload['level']}</code>\n"
        f"💾 <b>Uses</b>: <code>{payload['uses']}</code></blockquote>"
    )


def _build_buttons() -> list[list[Button]] | None:
    startup = _startup_status()
    buttons: list[Button] = [
        Button.url("News", "https://t.me/Death_Telega"),
        Button.url("Offtop", "https://t.me/Death_TgOfftop"),
    ]
    bot_name = str(startup.get("bot", {}).get("username") or "").strip()
    if bot_name:
        buttons.insert(0, Button.url("Inline Bot", f"https://t.me/{bot_name}"))
    if not buttons:
        return None
    rows: list[list[Button]] = []
    for index in range(0, len(buttons), 2):
        rows.append(buttons[index : index + 2])
    return rows


@command(
    "info",
    description="Render the DeathTG status card",
    usage=".info",
    aliases=("me", "profile", "i", "инфо"),
)
async def info_cmd(event, args: list[str]) -> None:
    status = await event.edit("<b>DeathTG:</b> building info card...", parse_mode="html")
    me = await event.client.get_me()
    cfg = load_config()
    settings = profile_settings()

    total = await usage_total()
    days = await installed_days()
    level = await level_info()
    modules = await top_modules(4)
    usage_rows = await usage_by_day(30)

    title = settings.get("profile_title") or "DeathTG Operator"
    name = " ".join(filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")])).strip()
    username = f"@{me.username}" if getattr(me, "username", None) else "@not_connected"
    description = settings.get("description") or "DeathTG userbot online."
    version, branch = _git_identity()
    card_path = render_info_card(
        title=title,
        username=username,
        description=description,
        prefix=cfg.command_prefix,
        uses=total,
        days=days,
        level=int(level["level"]),
        level_current=int(level["current"]),
        top_modules_text=_top_modules_text(modules),
        usage_points=_usage_chart_points(usage_rows),
        accent=settings.get("accent") or "blue",
    )
    startup = _startup_status()
    bot = startup.get("bot", {})
    inline_missing = not (bot.get("username") and bot.get("valid_username"))

    payload = {
        "title": title,
        "name": name or "DeathTG User",
        "username": username,
        "description": description,
        "role": settings.get("role", "user"),
        "prefix": cfg.command_prefix,
        "uses": total,
        "days": days,
        "level": level["level"],
        "level_current": level["current"],
        "top_modules": _top_modules_text(modules),
        "version": version,
        "branch": branch,
    }
    caption_lines = [_info_caption(settings, payload)]
    if inline_missing:
        caption_lines.extend(["", "Inline bot missing. Open Profile in the panel and run sync."])
    buttons = _build_buttons()
    try:
        await event.client.send_file(
            event.chat_id,
            file=str(card_path),
            caption="\n".join(caption_lines),
            buttons=buttons,
            parse_mode="html",
        )
        with contextlib.suppress(Exception):
            await status.delete()
    except Exception:
        plain = (
            f"<b>{title}</b>\n"
            f"<code>{username}</code>\n"
            f"{description}\n\n"
            f"<b>Prefix:</b> <code>{cfg.command_prefix}</code>\n"
            f"<b>Actions:</b> <code>{total}</code>\n"
            f"<b>Level:</b> <code>{level['level']}</code> ({level['current']}/100)\n"
            f"<b>Top modules:</b> <code>{_top_modules_text(modules)}</code>"
        )
        await status.edit(plain, parse_mode="html")
