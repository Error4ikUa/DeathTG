from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from deathtg.config import MODULES_DIR, ROOT_DIR, load_config
from deathtg.loader import ModuleLoader
from deathtg.metrics import installed_days, level_info, top_modules, usage_by_day, usage_total
from deathtg.registry import CommandRegistry, PROTECTED_MODULES
from deathtg.security import scan_module_source

PANEL_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PANEL_DIR / "templates"
STATIC_DIR = PANEL_DIR / "static"
MODULE_REPO_INDEX = os.getenv("MODULE_REPO_INDEX", "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/index.json")

app = FastAPI(title="DeathTG Panel")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("PANEL_SECRET", secrets.token_hex(32)), same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

registry = CommandRegistry()
loader = ModuleLoader(registry, MODULES_DIR)


def panel_key() -> str:
    return os.getenv("PANEL_PASSWORD", "deathtg")


def is_ready() -> bool:
    return (ROOT_DIR / ".env").exists() and bool(list(ROOT_DIR.glob("*.session")))


def is_authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


def gate(request: Request):
    if not is_ready():
        return RedirectResponse("/setup", status_code=303)
    if not is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return None


async def refresh_modules() -> None:
    registry._commands.clear()
    registry._aliases.clear()
    loader.loaded.clear()
    await loader.load_builtin("deathtg.modules", ["core", "system", "antivirus", "terminal"])
    await loader.load_all_local()


def write_env(api_id: int, api_hash: str, session_name: str, phone: str, panel_secret: str, bot_token: str = "") -> None:
    key = secrets.token_urlsafe(18)
    text = f"API_ID={api_id}\nAPI_HASH={api_hash}\nSESSION_NAME={session_name}\nCOMMAND_PREFIX=.\nOWNER_ID=\nBOT_TOKEN={bot_token}\nPANEL_PASSWORD={key}\nPANEL_SECRET={panel_secret}\nPHONE={phone}\n"
    (ROOT_DIR / ".env").write_text(text, encoding="utf-8")
    os.environ["PANEL_PASSWORD"] = key
    os.environ["PANEL_SECRET"] = panel_secret


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


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request, "step": "start", "error": None})


@app.post("/setup/save")
async def setup_save(request: Request, api_id: int = Form(...), api_hash: str = Form(...), phone: str = Form(...), session_name: str = Form("deathtg"), panel_secret: str = Form("change_me_long_secret"), bot_token: str = Form("")):
    try:
        write_env(api_id, api_hash, session_name, phone, panel_secret, bot_token)
        request.session["auth"] = True
        return RedirectResponse("/?message=Config saved. Now run python main.py once to finish Telegram login", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("setup.html", {"request": request, "step": "start", "error": f"{type(exc).__name__}: {exc}"})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not is_ready():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, key: str = Form(...)):
    if secrets.compare_digest(key, panel_key()):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный ключ"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    locked = gate(request)
    if locked:
        return locked
    await refresh_modules()
    return templates.TemplateResponse("dashboard.html", {"request": request, "status": status_data(), "profile": await profile_info(), "modules": loader.loaded, "grouped": registry.by_module(), "protected": PROTECTED_MODULES, "usage_days": usage_by_day(), "top_modules": top_modules(), "browser_modules": await browser_items(), "message": request.query_params.get("message"), "error": request.query_params.get("error")})


@app.post("/modules/download")
async def download_module(request: Request, link: str = Form(...)):
    locked = gate(request)
    if locked: return locked
    try:
        path = await loader.download_module(link)
        module_name = await loader.load_file(path)
        return RedirectResponse(f"/?message=Module {module_name} installed", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/modules/upload")
async def upload_module(request: Request, file: UploadFile = File(...)):
    locked = gate(request)
    if locked: return locked
    try:
        if not file.filename or not file.filename.endswith(".py"):
            raise RuntimeError("Нужен .py файл")
        text = (await file.read()).decode("utf-8")
        report = scan_module_source(text)
        if not report.allowed:
            raise RuntimeError("Модуль заблокирован защитой: " + report.pretty())
        target = MODULES_DIR / Path(file.filename).name
        target.write_text(text, encoding="utf-8")
        module_name = await loader.load_file(target)
        return RedirectResponse(f"/?message=Module {module_name} uploaded", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/modules/{name}/unload")
async def unload_module(request: Request, name: str):
    locked = gate(request)
    if locked: return locked
    try:
        loader.unload(name)
        return RedirectResponse(f"/?message=Module {name} unloaded", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/modules/{name}/delete")
async def delete_module(request: Request, name: str):
    locked = gate(request)
    if locked: return locked
    try:
        if name in PROTECTED_MODULES:
            raise RuntimeError("Protected module")
        loader.unload(name, silent=True)
        path = MODULES_DIR / f"{Path(name).name}.py"
        if path.exists(): path.unlink()
        return RedirectResponse(f"/?message=Module {name} deleted", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/scan")
async def scan_text(request: Request, source: str = Form(...)):
    locked = gate(request)
    if locked: return locked
    report = scan_module_source(source)
    verdict = "ALLOWED" if report.allowed else "BLOCKED"
    return templates.TemplateResponse("scan.html", {"request": request, "report": report, "verdict": verdict, "source": source})


@app.post("/update")
async def update_project(request: Request):
    locked = gate(request)
    if locked: return locked
    try:
        result = subprocess.run(["git", "pull"], cwd=ROOT_DIR, text=True, capture_output=True, timeout=60)
        output = (result.stdout + "\n" + result.stderr).strip()
        return RedirectResponse(f"/?message={output[-180:]}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)
