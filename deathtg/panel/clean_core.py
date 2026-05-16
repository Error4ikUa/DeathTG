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
import re
import ast
from functools import lru_cache
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from fastapi.templating import Jinja2Templates

from deathtg.assets import IMAGES_DIR, MODULE_IMAGES_DIR, local_module_image_path, module_image_path, resolve_module_entry, shared_module_image_path
from deathtg.config import MODULES_DIR, ROOT_DIR, RUNTIME_DIR, load_config
from deathtg.loader import ModuleLoader
from deathtg.metrics import installed_days, level_info, top_modules, usage_by_day, usage_total
from deathtg.profile_store import profile_settings
from deathtg.registry import CommandRegistry
from deathtg.security import is_trusted_module_link
from deathtg.startup_sync import STATUS_PATH


PANEL_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PANEL_DIR / "templates"
STATIC_DIR = PANEL_DIR / "static"
USER_STATIC_DIR = STATIC_DIR / "user"
MODULE_META_PATH = RUNTIME_DIR / "module_meta.json"

MODULE_REPO_INDEX = os.getenv(
    "MODULE_REPO_INDEX",
    "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/index.json",
)
MODULE_REPO_API = os.getenv(
    "MODULE_REPO_API", "https://api.github.com/repos/Error4ikUa/DTG_Modules/contents?ref=main"
)
PROTECTED_BASE_MODULES = {"core", "root", "info", "system", "antivirus", "terminal"}

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
    """Return True if the configuration file has the API credentials needed to start."""
    env = ROOT_DIR / ".env"
    if not env.exists():
        return False
    values: dict[str, str] = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return bool(values.get("API_ID") and values.get("API_HASH"))


def has_session() -> bool:
    """Return True if there is at least one Telethon session file."""
    return bool(list(ROOT_DIR.glob("*.session")))


def avatar_url() -> str:
    """Return the URL to the user's avatar if one has been saved."""
    USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("avatar.png", "avatar.jpg", "avatar.jpeg", "avatar.webp"):
        path = USER_STATIC_DIR / name
        if path.exists():
            try:
                stamp = int(path.stat().st_mtime)
            except OSError:
                stamp = 0
            return f"/static/user/{name}?t={stamp}"
    return ""


async def refresh_modules() -> None:
    """Clear and reload all built‑in and local modules."""
    registry._commands.clear()
    registry._aliases.clear()
    loader.loaded.clear()
    await loader.load_builtin(
        "deathtg.modules", ["core", "root", "info", "system", "antivirus", "terminal"]
    )
    meta = load_module_meta()
    verified = {
        name
        for name, item in meta.items()
        if isinstance(item, dict) and (item.get("verified") or item.get("security_override"))
    }
    await loader.load_all_local(force_modules=verified)


def startup_status() -> dict:
    if STATUS_PATH.exists():
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            default = _default_startup_status()
            if isinstance(data, dict):
                data.setdefault("bot", default["bot"])
                data.setdefault("helper_bot", default["helper_bot"])
                data["bot"].setdefault("role", "inline")
                data["helper_bot"].setdefault("role", "helper")
                bots = data.get("bots")
                if not isinstance(bots, list) or len(bots) < 2:
                    data["bots"] = [data.get("bot", default["bot"]), data.get("helper_bot", default["helper_bot"])]
                data.setdefault("channels", [])
                data.setdefault("folder", default["folder"])
                data.setdefault("last_sync_at", None)
                data.setdefault("last_sync_error", None)
                return data
        except Exception:
            pass
    return _default_startup_status()


def load_module_meta() -> dict[str, dict]:
    if MODULE_META_PATH.exists():
        try:
            data = json.loads(MODULE_META_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _default_startup_status() -> dict:
    bot = {
        "role": "inline",
        "configured": False,
        "username": "",
        "created": False,
        "valid_username": False,
        "expected_prefix": "",
        "owner_id": None,
        "commands_synced": False,
        "inline_synced": False,
        "avatar_synced": False,
        "archived": False,
        "error": None,
    }
    helper = {**bot, "role": "helper", "inline_synced": False}
    return {
        "bot": bot,
        "helper_bot": helper,
        "bots": [bot, helper],
        "channels": [],
        "folder": {"name": "DeathTG", "ok": False, "error": None, "include_count": 0, "include_usernames": []},
        "last_sync_at": None,
        "last_sync_error": None,
    }


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
                "role": settings.get("role", "admin"),
                "info_text": settings.get("info_text", ""),
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
        "role": settings.get("role", "admin"),
        "info_text": settings.get("info_text", ""),
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
    level = await level_info()

    return {
        "config_ok": cfg_ok,
        "session_file": has_session(),
        "session_ok": profile.get("ok") == "1",
        "prefix": prefix,
        "modules_count": len(loader.loaded),
        "commands_count": len(list(registry.all())),
        "uses": uses,
        "days": days,
        "level": level,
    }


async def _module_repo_from_index(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(MODULE_REPO_INDEX, timeout=10) as r:
        if r.status != 200:
            return []
        data = await r.json()
        items = data.get("modules", []) if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        return [_normalize_repo_item(dict(item)) for item in items if isinstance(item, dict)]


def _raw_sibling_url(download_url: str, name: str) -> str:
    if not download_url or "/" not in download_url:
        return ""
    return download_url.rsplit("/", 1)[0] + "/" + name


async def _url_exists(session: aiohttp.ClientSession, url: str) -> bool:
    if not url:
        return False
    try:
        async with session.head(url, timeout=7) as response:
            if response.status == 200:
                return True
    except Exception:
        pass
    try:
        async with session.get(url, timeout=7) as response:
            return response.status == 200
    except Exception:
        return False


async def _discover_image(session: aiohttp.ClientSession, download_url: str, stem: str) -> str:
    candidates = [
        _raw_sibling_url(download_url, "Module.png"),
        _raw_sibling_url(download_url, "Image.png"),
        _raw_sibling_url(download_url, "image.png"),
        _raw_sibling_url(download_url, f"{stem}.png"),
        _raw_sibling_url(download_url, f"{stem}.jpg"),
        _raw_sibling_url(download_url, f"{stem}.webp"),
    ]
    for candidate in candidates:
        if await _url_exists(session, candidate):
            return candidate
    return ""


def _normalize_repo_item(item: dict) -> dict:
    link = str(item.get("link") or item.get("raw") or item.get("url") or item.get("download_url") or "")
    image = str(item.get("image") or item.get("Image") or item.get("Module.png") or item.get("Image.png") or item.get("modul_png") or "")
    name = str(item.get("name") or Path(link).stem or "module")
    description = str(item.get("description") or f"{name} module from DTG_Modules")
    return {
        **item,
        "name": name,
        "description": description,
        "image": image,
        "modul_png": image,
        "link": link,
        "author": str(item.get("author") or "DTG"),
        "version": str(item.get("version") or "latest"),
        "keywords": str(item.get("keywords") or name.replace("_", " ")),
    }


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
        item_type = str(item.get("type") or "")
        if item_type == "dir":
            dir_url = str(item.get("url") or "")
            if not dir_url:
                continue
            async with session.get(dir_url, timeout=10) as sub_response:
                if sub_response.status != 200:
                    continue
                sub_items = await sub_response.json()
            if not isinstance(sub_items, list):
                continue
            py_item = next(
                (
                    sub
                    for sub in sub_items
                    if str(sub.get("name") or "").endswith(".py")
                    and not str(sub.get("name") or "").startswith("_")
                ),
                None,
            )
            if not py_item:
                continue
            image_item = next(
                (
                    sub
                    for sub in sub_items
                    if str(sub.get("name") or "").lower() in {"module.png", "image.png"}
                ),
                None,
            )
            stem = Path(str(py_item.get("name") or name)).stem
            modules.append(
                _normalize_repo_item(
                    {
                        "name": name or stem,
                        "description": f"{name or stem} module from DTG_Modules",
                        "image": image_item.get("download_url") if image_item else "",
                        "link": py_item.get("download_url") or py_item.get("html_url") or "",
                        "keywords": (name or stem).replace("_", " "),
                    }
                )
            )
            continue
        if name.endswith(".py") and not name.startswith("_"):
            stem = name[:-3]
            link = item.get("download_url") or item.get("html_url") or ""
            modules.append(
                _normalize_repo_item(
                    {
                        "name": stem,
                        "description": f"{stem} module from DTG_Modules",
                        "image": await _discover_image(session, str(item.get("download_url") or ""), stem),
                        "link": link,
                        "keywords": stem.replace("_", " "),
                    }
                )
            )
    return modules


async def module_repo() -> list[dict]:
    """Return a list of available modules from the configured repository."""
    try:
        async with aiohttp.ClientSession() as s:
            indexed = await _module_repo_from_index(s)
            items = indexed or await _module_repo_from_github_contents(s)
            for item in items:
                link = str(item.get("link") or item.get("raw") or item.get("url") or "")
                if not item.get("image") and link:
                    stem = Path(link.split("?", 1)[0]).stem
                    item["image"] = await _discover_image(s, link, stem)
                    item["modul_png"] = item["image"]
                item["image"] = repo_module_image_url(str(item.get("name") or ""), str(item.get("image") or ""))
                item["modul_png"] = item["image"]
                item["verified"] = is_trusted_module_link(link)
                item["source_label"] = "Verified by DTG" if item["verified"] else "External source"
            return items
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


def module_image_url(module_name: str, meta_image: str = "") -> str:
    if meta_image:
        return meta_image
    local = local_module_image_path(module_name)
    if local and local.exists():
        try:
            stamp = int(local.stat().st_mtime)
        except OSError:
            stamp = 0
        return f"/module-media/{module_name}?t={stamp}"
    shared = shared_module_image_path(module_name)
    if shared and shared.exists() and IMAGES_DIR in shared.parents:
        try:
            stamp = int(shared.stat().st_mtime)
        except OSError:
            stamp = 0
        relative = shared.relative_to(IMAGES_DIR).as_posix()
        return f"/images/{relative}?t={stamp}"
    return ""


def repo_module_image_url(module_name: str, remote_image: str = "") -> str:
    if remote_image:
        return remote_image
    shared = shared_module_image_path(module_name)
    if shared and shared.exists() and IMAGES_DIR in shared.parents:
        try:
            stamp = int(shared.stat().st_mtime)
        except OSError:
            stamp = 0
        relative = shared.relative_to(IMAGES_DIR).as_posix()
        return f"/images/{relative}?t={stamp}"
    return ""


def installed_module_cards(grouped: dict[str, list] | None = None) -> dict[str, dict]:
    grouped = grouped or registry.by_module()
    meta_map = load_module_meta()
    cards: dict[str, dict] = {}
    for module_name, commands in grouped.items():
        meta = meta_map.get(module_name, {})
        description = str(meta.get("description") or "")
        if not description:
            description = "Protected DeathTG module" if module_name in PROTECTED_BASE_MODULES else "Installed external module"
        cards[module_name] = {
            "name": module_name,
            "description": description,
            "image": module_image_url(module_name, str(meta.get("image") or "")),
            "verified": bool(meta.get("verified")),
            "protected": module_name in PROTECTED_BASE_MODULES,
            "commands": commands,
        }
    return cards


async def module_detail(module_name: str) -> dict:
    grouped = registry.by_module()
    commands = grouped.get(module_name, [])
    meta = load_module_meta().get(module_name, {})
    path = loader.module_source_path(module_name) or (MODULES_DIR / f"{module_name}.py")
    if not path.exists():
        builtin_path = ROOT_DIR / "deathtg" / "modules" / f"{module_name}.py"
        if builtin_path.exists():
            path = builtin_path
    source = ""
    if path.exists():
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            source = ""
    parsed_meta = _extract_module_source_meta(source)
    description = str(meta.get("description") or "")
    if not description and commands:
        description = f"{module_name} provides {len(commands)} DeathTG command(s)."
    if not description:
        description = f"{module_name} core DeathTG module."
    image = module_image_url(module_name, str(meta.get("image") or ""))
    instances = loader.instances.get(module_name, [])
    config_values = []
    for inst in instances:
        cfg = getattr(inst, "config", None)
        if hasattr(cfg, "values") and hasattr(cfg, "dump"):
            dumped = cfg.dump(include_secrets=False)
            for value in cfg.values():
                value_name = getattr(value, "name", "")
                current_value = dumped.get(value_name, "")
                value_type = type(getattr(value, "validator", None)).__name__
                if not value_type or value_type == "NoneType":
                    value_type = type(current_value).__name__
                config_values.append(
                    {
                        "name": value_name,
                        "description": getattr(value, "description", ""),
                        "value": current_value,
                        "secret": bool(getattr(value, "secret", False)),
                        "type": value_type,
                    }
                )
    handler_counts = {
        "watchers": len(loader.watchers.get(module_name, [])),
        "raw": len(loader.raw_handlers.get(module_name, [])),
        "inline": len(loader.inline_handlers.get(module_name, [])),
        "callbacks": len(loader.callback_handlers.get(module_name, [])),
    }
    return {
        "name": module_name,
        "commands": commands,
        "meta": meta,
        "protected": module_name in PROTECTED_BASE_MODULES,
        "description": description,
        "image": image,
        "scopes": parsed_meta["scopes"],
        "requires": parsed_meta["requires"],
        "hikka_meta": parsed_meta["meta"],
        "config_values": config_values,
        "handler_counts": handler_counts,
        "security": {
            "verdict": meta.get("security_verdict") or ("VERIFIED" if meta.get("verified") else "LOCAL"),
            "score": meta.get("security_score", 0),
            "findings": meta.get("security_findings") or [],
            "override": bool(meta.get("security_override")),
        },
        "source_preview": source[:5000],
        "path": str(path) if path.exists() else "",
        "repo": False,
        "install_link": "",
    }


async def repo_module_detail(module_name: str) -> dict:
    items = await module_repo()
    selected = None
    needle = module_name.strip().lower()
    for item in items:
        if str(item.get("name") or "").strip().lower() == needle:
            selected = item
            break
    if not selected:
        return {
            "name": module_name,
            "commands": [],
            "meta": {},
            "protected": False,
            "description": "Repository module was not found.",
            "image": "",
            "scopes": [],
            "requires": [],
            "hikka_meta": {},
            "config_values": [],
        "handler_counts": {"watchers": 0, "raw": 0, "inline": 0, "callbacks": 0},
        "security": {"verdict": "REPO", "score": 0, "findings": [], "override": False},
            "source_preview": "",
            "path": "",
            "repo": True,
            "install_link": "",
        }
    link = str(selected.get("link") or selected.get("raw") or selected.get("url") or "")
    source = ""
    if link:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=20) as response:
                    if response.status == 200:
                        source = await response.text()
        except Exception:
            source = ""
    parsed_meta = _extract_module_source_meta(source)
    commands = _extract_source_commands(source)
    name = str(selected.get("name") or module_name)
    return {
        "name": name,
        "commands": commands,
        "meta": {
            "author": selected.get("author") or "DTG",
            "version": selected.get("version") or "latest",
            "source_link": link,
            "verified": bool(selected.get("verified")),
        },
        "protected": False,
        "description": str(selected.get("description") or f"{name} module from DTG_Modules"),
        "image": repo_module_image_url(name, str(selected.get("image") or selected.get("modul_png") or "")),
        "scopes": parsed_meta["scopes"],
        "requires": parsed_meta["requires"],
        "hikka_meta": parsed_meta["meta"],
        "config_values": [],
        "handler_counts": {"watchers": 0, "raw": 0, "inline": 0, "callbacks": 0},
        "security": {"verdict": "VERIFIED" if selected.get("verified") else "EXTERNAL", "score": 0, "findings": [], "override": False},
        "source_preview": source[:5000],
        "path": "repository",
        "repo": True,
        "install_link": link,
    }


def _extract_module_source_meta(source: str) -> dict:
    scopes: list[str] = []
    requires: list[str] = []
    meta: dict[str, str] = {}
    if not source:
        return {"scopes": scopes, "requires": requires, "meta": meta}
    for line in source.splitlines()[:80]:
        stripped = line.strip()
        scope_match = re.match(r"#\s*scope:\s*(.+)$", stripped, flags=re.I)
        if scope_match:
            scopes.extend(part.strip() for part in scope_match.group(1).split() if part.strip())
            continue
        requires_match = re.match(r"#\s*requires:\s*(.+)$", stripped, flags=re.I)
        if requires_match:
            requires.extend(part.strip() for part in requires_match.group(1).split() if part.strip())
            continue
        meta_match = re.match(r"#\s*meta\s+([a-z0-9_-]+):\s*(.+)$", stripped, flags=re.I)
        if meta_match:
            meta[meta_match.group(1).lower()] = meta_match.group(2).strip()
    return {"scopes": sorted(set(scopes)), "requires": sorted(set(requires)), "meta": meta}


def _extract_source_commands(source: str) -> list[dict]:
    if not source:
        return []
    commands: list[dict] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return commands
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            name = ""
            if isinstance(deco.func, ast.Name):
                name = deco.func.id
            elif isinstance(deco.func, ast.Attribute):
                name = deco.func.attr
            if name != "command":
                continue
            command_name = ""
            description = ""
            usage = ""
            if deco.args and isinstance(deco.args[0], ast.Constant):
                command_name = str(deco.args[0].value)
            for kw in deco.keywords:
                if not isinstance(kw.value, ast.Constant):
                    continue
                if kw.arg == "description":
                    description = str(kw.value.value)
                if kw.arg == "usage":
                    usage = str(kw.value.value)
            if not command_name and node.name.endswith("_cmd"):
                command_name = node.name[:-4]
            if command_name:
                commands.append({"name": command_name, "description": description, "usage": usage, "aliases": ()})
    return commands
