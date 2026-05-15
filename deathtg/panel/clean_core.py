from __future__ import annotations

"""
Core utilities for the DeathTG panel.

This module centralises configuration loading, template initialisation,
profile retrieval and statistics collection.  It exposes various
helpers used across the panel:

* ``env_load``: load environment variables from ``.env``.
* ``panel_password``: cached retrieval of the panel password from
  ``.env`` (default ``deathtg``).
* ``has_env``/``has_session``: check for existence of configuration
  files and session files.
* ``avatar_url``: build a URL for the saved user avatar.
* ``profile_info``: return a dictionary with the user's profile
  information (read from ``profile.json`` if present, else defaults).
* ``status``: return a summary of installation status and usage stats.
* ``module_repo``: fetch available modules from the official module
  index or fallback to GitHub contents API.
* ``activity_points``: aggregate module usage by day for charting.

Most functions here are asynchronous because they depend on the
asynchronous metrics API.  Always remember to ``await`` these functions
when calling them from FastAPI route handlers.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

from deathtg.config import MODULES_DIR, ROOT_DIR, RUNTIME_DIR, load_config
from deathtg.loader import ModuleLoader
from deathtg.metrics import installed_days, top_modules, usage_by_day, usage_total
from deathtg.profile_store import profile_settings
from deathtg.registry import CommandRegistry


PANEL_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PANEL_DIR / "templates"
STATIC_DIR = PANEL_DIR / "static"
USER_STATIC_DIR = STATIC_DIR / "user"

MODULE_REPO_INDEX = os.getenv(
    "MODULE_REPO_INDEX",
    "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/index.json",
)
MODULE_REPO_API = os.getenv(
    "MODULE_REPO_API", "https://api.github.com/repos/Error4ikUa/DTG_Modules/contents?ref=main"
)

templates = Jinja2Templates(directory=TEMPLATES_DIR)
registry = CommandRegistry()
loader = ModuleLoader(registry, MODULES_DIR)


def env_load() -> None:
    """Load environment variables from ``.env`` into the process.

    This uses ``dotenv.load_dotenv`` to pull variables from a file at
    the root of the repository.  The ``override=True`` flag allows
    environment variables in the file to override existing ones.
    """
    load_dotenv(ROOT_DIR / ".env", override=True)


@lru_cache(maxsize=1)
def panel_password() -> str:
    """Return the panel password from the ``.env`` file.

    Cached on first call to avoid repeatedly reading the file.  Falls
    back to the ``PANEL_PASSWORD`` environment variable or the
    default ``deathtg``.
    """
    env_load()
    env = ROOT_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PANEL_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return os.getenv("PANEL_PASSWORD", "deathtg")


def has_env() -> bool:
    """Return True if the configuration file ``.env`` exists."""
    return (ROOT_DIR / ".env").exists()


def has_session() -> bool:
    """Return True if there is at least one Telethon session file."""
    return bool(list(ROOT_DIR.glob("*.session")))


def avatar_url() -> str:
    """Return the URL to the user's avatar if one has been saved."""
    USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("avatar.png", "avatar.jpg", "avatar.jpeg", "avatar.webp"):
        if (USER_STATIC_DIR / name).exists():
            return f"/static/user/{name}"
    return ""


async def refresh_modules() -> None:
    """Clear and reload all built‑in and local modules."""
    registry._commands.clear()
    registry._aliases.clear()
    loader.loaded.clear()
    await loader.load_builtin(
        "deathtg.modules", ["core", "system", "antivirus", "terminal"]
    )
    await loader.load_all_local()


async def profile_info() -> dict[str, str]:
    """Return a dictionary describing the user.

    Reads data from ``runtime/profile.json`` if present.  Falls back
    to values from ``profile_settings`` and sensible defaults.
    """
    avatar = avatar_url()
    settings = profile_settings()
    path = RUNTIME_DIR / "profile.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "name": data.get("name") or "DeathTG User",
                "username": data.get("username") or "",
                "id": str(data.get("id") or "unknown"),
                "ok": data.get("ok") or "1",
                "avatar": avatar,
                "description": settings.get("description", ""),
                "language": settings.get("language", "en"),
                "accent": settings.get("accent", "blue"),
                "profile_title": settings.get("profile_title", "DeathTG Operator"),
            }
        except Exception:
            pass
    return {
        "name": "DeathTG User",
        "username": "not connected",
        "id": "unknown",
        "ok": "0",
        "avatar": avatar,
        "description": settings.get("description", ""),
        "language": settings.get("language", "en"),
        "accent": settings.get("accent", "blue"),
        "profile_title": settings.get("profile_title", "DeathTG Operator"),
    }


async def status(profile: dict[str, str]) -> dict:
    """Return a summary of configuration and usage status.

    The returned dictionary includes flags for whether the config
    exists, whether a Telethon session is present, the current
    command prefix and counts of modules, commands, and usage
    statistics.
    """
    try:
        cfg = load_config()
        cfg_ok = True
        prefix = cfg.command_prefix
    except Exception:
        cfg_ok = False
        prefix = "."

    uses = await usage_total()
    days = await installed_days()

    return {
        "config_ok": cfg_ok,
        "session_file": has_session(),
        "session_ok": profile.get("ok") == "1",
        "prefix": prefix,
        "modules_count": len(loader.loaded),
        "commands_count": len(list(registry.all())),
        "uses": uses,
        "days": days,
    }


async def _module_repo_from_index(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(MODULE_REPO_INDEX, timeout=10) as r:
        if r.status != 200:
            return []
        data = await r.json()
        items = data.get("modules", data if isinstance(data, list) else [])
        return items if isinstance(items, list) else []


async def _module_repo_from_github_contents(
    session: aiohttp.ClientSession,
) -> list[dict]:
    async with session.get(MODULE_REPO_API, timeout=10) as r:
        if r.status != 200:
            return []
        data = await r.json()
    if not isinstance(data, list):
        return []
    modules: list[dict] = []
    for item in data:
        name = str(item.get("name") or "")
        if not name.endswith(".py") or name.startswith("_"):
            continue
        stem = name[:-3]
        modules.append(
            {
                "name": stem,
                "description": f"{stem} module from DTG_Modules",
                "image": "",
                "link": item.get("download_url") or item.get("html_url") or "",
                "keywords": stem.replace("_", " "),
            }
        )
    return modules


async def module_repo() -> list[dict]:
    """Return a list of available modules from the configured repository."""
    try:
        async with aiohttp.ClientSession() as s:
            indexed = await _module_repo_from_index(s)
            if indexed:
                return indexed
            return await _module_repo_from_github_contents(s)
    except Exception:
        return []


async def activity_points() -> list[dict]:
    """Aggregate usage by day for the last 30 days.

    Returns a list of dictionaries with ``day``, ``count`` and
    ``modules`` keys, sorted by day.
    """
    grouped = {}
    rows = await usage_by_day(30)
    for row in rows:
        grouped.setdefault(str(row.get("day")), set()).add(str(row.get("module")))
    return [
        {"day": d, "count": len(m), "modules": sorted(m)} for d, m in sorted(grouped.items())
    ]