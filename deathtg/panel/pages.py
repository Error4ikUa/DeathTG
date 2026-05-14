from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from deathtg.config import MODULES_DIR, ROOT_DIR, load_config
from deathtg.loader import ModuleLoader
from deathtg.metrics import installed_days, level_info, top_modules, usage_by_day, usage_total
from deathtg.registry import CommandRegistry, PROTECTED_MODULES
from deathtg.security import scan_module_source

router = APIRouter()
PANEL_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PANEL_DIR / "templates"
MODULE_REPO_INDEX = os.getenv("MODULE_REPO_INDEX", "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/index.json")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
registry = CommandRegistry()
loader = ModuleLoader(registry, MODULES_DIR)


def ready() -> bool:
    return (ROOT_DIR / ".env").exists() and bool(list(ROOT_DIR.glob("*.session")))


def guard(request: Request):
    if not ready():
        return RedirectResponse("/setup", status_code=303)
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return None


async def refresh_modules() -> None:
    registry._commands.clear()
    registry._aliases.clear()
    loader.loaded.clear()
    await loader.load_builtin("deathtg.modules", ["core", "system", "antivirus", "terminal"])
    await loader.load_all_local()


async def profile_info() -> dict[str, str]:
    try:
        from telethon import TelegramClient
        cfg = load_config()
        client = TelegramClient(str(ROOT_DIR / cfg.session_name), cfg.api_id, cfg.api_hash)
        await client.connect()
        me = await client.get_me()
        await client.disconnect()
        name = " ".join([me.first_name or "", me.last_name or ""]).strip() or "DeathTG User"
        return {"name": name, "username": me.username or "", "id": str(me.id)}
    except Exception:
        return {"name": "DeathTG User", "username": "not connected", "id": ""}


def status_data() -> dict[str, Any]:
    try:
        cfg = load_config()
        cfg_ok = True
        prefix = cfg.command_prefix
    except Exception:
        cfg_ok = False
        prefix = "."
    return {"env_exists": (ROOT_DIR / ".env").exists(), "session_exists": bool(list(ROOT_DIR.glob("*.session"))), "config_ok": cfg_ok, "prefix": prefix, "modules_count": len(loader.loaded), "commands_count": len(list(registry.all())), "uses": usage_total(), "days": installed_days(), "level": level_info()}


async def browser_items() -> list[dict[str, Any]]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(MODULE_REPO_INDEX, timeout=10) as response:
                if response.status != 200:
                    return []
                data = await response.json()
                return data.get("modules", data if isinstance(data, list) else [])
    except Exception:
        return []


async def base_ctx(request: Request) -> dict[str, Any]:
    await refresh_modules()
    return {"request": request, "status": status_data(), "profile": await profile_info(), "protected": PROTECTED_MODULES, "message": request.query_params.get("message"), "error": request.query_params.get("error")}


@router.get("/activity")
async def activity_page(request: Request):
    blocked = guard(request)
    if blocked: return blocked
    ctx = await base_ctx(request)
    ctx.update({"usage_days": usage_by_day(), "top_modules": top_modules()})
    return templates.TemplateResponse("activity.html", ctx)


@router.get("/browser")
async def browser_page(request: Request):
    blocked = guard(request)
    if blocked: return blocked
    ctx = await base_ctx(request)
    ctx.update({"browser_modules": await browser_items()})
    return templates.TemplateResponse("browser.html", ctx)


@router.get("/installed")
async def installed_page(request: Request):
    blocked = guard(request)
    if blocked: return blocked
    ctx = await base_ctx(request)
    ctx.update({"grouped": registry.by_module()})
    return templates.TemplateResponse("installed.html", ctx)


@router.get("/install")
async def install_page(request: Request):
    blocked = guard(request)
    if blocked: return blocked
    ctx = await base_ctx(request)
    return templates.TemplateResponse("install.html", ctx)


@router.get("/scanner")
async def scanner_page(request: Request):
    blocked = guard(request)
    if blocked: return blocked
    ctx = await base_ctx(request)
    return templates.TemplateResponse("scanner.html", ctx)


@router.post("/scanner/check")
async def scanner_check(request: Request, source: str = Form(""), link: str = Form(""), file: UploadFile | None = File(None)):
    blocked = guard(request)
    if blocked: return blocked
    try:
        text = source
        if file and file.filename:
            text = (await file.read()).decode("utf-8")
        if link and not text:
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=20) as response:
                    text = await response.text()
        if not text.strip():
            raise RuntimeError("Нечего проверять: вставь ссылку, файл или код")
        report = scan_module_source(text)
        verdict = "ALLOWED" if report.allowed else "BLOCKED"
        ctx = await base_ctx(request)
        ctx.update({"report": report, "verdict": verdict})
        return templates.TemplateResponse("scanner.html", ctx)
    except Exception as exc:
        return RedirectResponse(f"/scanner?error={type(exc).__name__}: {exc}", status_code=303)
