from __future__ import annotations

import json
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

from deathtg.config import MODULES_DIR, ROOT_DIR, RUNTIME_DIR, load_config
from deathtg.loader import ModuleLoader
from deathtg.metrics import installed_days, top_modules, usage_by_day, usage_total
from deathtg.registry import CommandRegistry

PANEL_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PANEL_DIR / "templates"
STATIC_DIR = PANEL_DIR / "static"
USER_STATIC_DIR = STATIC_DIR / "user"
MODULE_REPO_INDEX = os.getenv("MODULE_REPO_INDEX", "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/index.json")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
registry = CommandRegistry()
loader = ModuleLoader(registry, MODULES_DIR)


def env_load() -> None:
    load_dotenv(ROOT_DIR / ".env", override=True)


def panel_password() -> str:
    env_load()
    env = ROOT_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PANEL_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return os.getenv("PANEL_PASSWORD", "deathtg")


def has_env() -> bool:
    return (ROOT_DIR / ".env").exists()


def has_session() -> bool:
    return bool(list(ROOT_DIR.glob("*.session")))


def avatar_url() -> str:
    USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("avatar.png", "avatar.jpg", "avatar.jpeg", "avatar.webp"):
        if (USER_STATIC_DIR / name).exists():
            return f"/static/user/{name}"
    return ""


async def refresh_modules() -> None:
    registry._commands.clear()
    registry._aliases.clear()
    loader.loaded.clear()
    await loader.load_builtin("deathtg.modules", ["core", "system", "antivirus", "terminal"])
    await loader.load_all_local()


async def profile_info() -> dict[str, str]:
    avatar = avatar_url()
    path = RUNTIME_DIR / "profile.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"name": data.get("name") or "DeathTG User", "username": data.get("username") or "", "id": str(data.get("id") or "unknown"), "ok": data.get("ok") or "1", "avatar": avatar}
        except Exception:
            pass
    return {"name": "DeathTG User", "username": "not connected", "id": "unknown", "ok": "0", "avatar": avatar}


def status(profile: dict[str, str]) -> dict:
    try:
        cfg = load_config(); cfg_ok = True; prefix = cfg.command_prefix
    except Exception:
        cfg_ok = False; prefix = "."
    return {"config_ok": cfg_ok, "session_file": has_session(), "session_ok": profile.get("ok") == "1", "prefix": prefix, "modules_count": len(loader.loaded), "commands_count": len(list(registry.all())), "uses": usage_total(), "days": installed_days()}


async def module_repo() -> list[dict]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(MODULE_REPO_INDEX, timeout=10) as r:
                if r.status != 200:
                    return []
                data = await r.json()
                return data.get("modules", data if isinstance(data, list) else [])
    except Exception:
        return []


def activity_points() -> list[dict]:
    grouped = {}
    for row in usage_by_day(30):
        grouped.setdefault(str(row.get("day")), set()).add(str(row.get("module")))
    return [{"day": d, "count": len(m), "modules": sorted(m)} for d, m in sorted(grouped.items())]
