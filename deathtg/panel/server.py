from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from deathtg.config import MODULES_DIR, ROOT_DIR, load_config
from deathtg.registry import CommandRegistry
from deathtg.loader import ModuleLoader
from deathtg.security import scan_module_source

PANEL_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PANEL_DIR / "templates"
STATIC_DIR = PANEL_DIR / "static"

app = FastAPI(title="DeathTG Panel")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("PANEL_SECRET", secrets.token_hex(32)),
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

registry = CommandRegistry()
loader = ModuleLoader(registry, MODULES_DIR)


def panel_password() -> str:
    return os.getenv("PANEL_PASSWORD", "deathtg")


def authed(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_auth(request: Request):
    if not authed(request):
        return RedirectResponse("/login", status_code=303)
    return None


async def refresh_modules() -> None:
    registry._commands.clear()
    registry._aliases.clear()
    loader.loaded.clear()
    await loader.load_builtin("deathtg.modules", ["core", "system"])
    await loader.load_all_local()


def bot_status() -> dict[str, Any]:
    session_files = list(ROOT_DIR.glob("*.session"))
    env_exists = (ROOT_DIR / ".env").exists()
    try:
        cfg = load_config()
        cfg_ok = True
        prefix = cfg.command_prefix
    except Exception:
        cfg_ok = False
        prefix = "."
    return {
        "env_exists": env_exists,
        "session_exists": bool(session_files),
        "config_ok": cfg_ok,
        "prefix": prefix,
        "modules_count": len(loader.loaded),
        "commands_count": len(list(registry.all())),
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if secrets.compare_digest(password, panel_password()):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный пароль"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    gate = require_auth(request)
    if gate:
        return gate
    await refresh_modules()
    grouped = registry.by_module()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "status": bot_status(),
            "modules": loader.loaded,
            "grouped": grouped,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@app.post("/modules/download")
async def download_module(request: Request, link: str = Form(...)):
    gate = require_auth(request)
    if gate:
        return gate
    try:
        path = await loader.download_module(link)
        module_name = await loader.load_file(path)
        return RedirectResponse(f"/?message=Module {module_name} installed", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/modules/upload")
async def upload_module(request: Request, file: UploadFile = File(...)):
    gate = require_auth(request)
    if gate:
        return gate
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
    gate = require_auth(request)
    if gate:
        return gate
    try:
        loader.unload(name)
        return RedirectResponse(f"/?message=Module {name} unloaded", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/modules/{name}/delete")
async def delete_module(request: Request, name: str):
    gate = require_auth(request)
    if gate:
        return gate
    try:
        loader.unload(name, silent=True)
        path = MODULES_DIR / f"{Path(name).name}.py"
        if path.exists():
            path.unlink()
        return RedirectResponse(f"/?message=Module {name} deleted", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/scan")
async def scan_text(request: Request, source: str = Form(...)):
    gate = require_auth(request)
    if gate:
        return gate
    report = scan_module_source(source)
    verdict = "ALLOWED" if report.allowed else "BLOCKED"
    return templates.TemplateResponse(
        "scan.html",
        {"request": request, "report": report, "verdict": verdict, "source": source},
    )


@app.post("/update")
async def update_project(request: Request):
    gate = require_auth(request)
    if gate:
        return gate
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            timeout=60,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return RedirectResponse(f"/?message={output[-180:]}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)
