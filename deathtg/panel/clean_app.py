from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

import aiohttp
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from deathtg.config import ROOT_DIR
from deathtg.metrics import init_metrics
from deathtg.panel.clean_actions import router as actions_router
from deathtg.panel.clean_core import (
    STATIC_DIR,
    activity_points,
    env_load,
    has_env,
    module_repo,
    panel_password,
    profile_info,
    refresh_modules,
    registry,
    status,
    templates,
    top_modules,
)
from deathtg.panel.re_auth import router as reconnect_router
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import scan_module_source
from deathtg.panel.auth_flow import write_env


env_load()

app = FastAPI(title="DeathTG Panel")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("PANEL_SECRET", secrets.token_hex(32)),
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(actions_router)
app.include_router(reconnect_router)


@app.on_event("startup")
async def startup_event() -> None:
    await init_metrics()
    await refresh_modules()


def _auth_guard(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return None


async def _base_context(request: Request) -> dict:
    profile = await profile_info()
    st = await status(profile)
    return {
        "request": request,
        "profile": profile,
        "status": st,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    }


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if has_env() and request.session.get("auth"):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request, "step": "start", "error": None})


@app.post("/setup/save")
async def setup_save(
    request: Request,
    api_id: int = Form(...),
    api_hash: str = Form(...),
    phone: str = Form(...),
    session_name: str = Form("deathtg"),
    panel_secret: str = Form("change_me_long_secret"),
    bot_token: str = Form(""),
):
    try:
        panel_key = secrets.token_urlsafe(18)
        write_env(api_id, api_hash, session_name, phone, panel_key, panel_secret, bot_token)
        os.environ["PANEL_PASSWORD"] = panel_key
        os.environ["PANEL_SECRET"] = panel_secret
        panel_password.cache_clear()
        request.session["auth"] = True
        return RedirectResponse("/reconnect?message=Config saved", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "start", "error": f"{type(exc).__name__}: {exc}"},
        )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, key: str = Form(...)):
    if secrets.compare_digest(key, panel_password()):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Неверный ключ"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    ctx = await _base_context(request)
    ctx["page"] = "home"
    return templates.TemplateResponse("clean_home.html", ctx)


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    return templates.TemplateResponse("clean_profile.html", await _base_context(request))


@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    ctx = await _base_context(request)
    ctx.update(
        {
            "activity_points": await activity_points(),
            "top_modules": await top_modules(),
        }
    )
    return templates.TemplateResponse("clean_activity.html", ctx)


@app.get("/browser", response_class=HTMLResponse)
async def browser_page(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    await refresh_modules()
    ctx = await _base_context(request)
    ctx.update(
        {
            "browser_modules": await module_repo(),
            "grouped": registry.by_module(),
            "protected": PROTECTED_MODULES,
        }
    )
    return templates.TemplateResponse("clean_browser.html", ctx)


@app.get("/installed", response_class=HTMLResponse)
async def installed_page(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    await refresh_modules()
    ctx = await _base_context(request)
    ctx.update({"grouped": registry.by_module(), "protected": PROTECTED_MODULES})
    return templates.TemplateResponse("clean_installed.html", ctx)


@app.get("/scanner", response_class=HTMLResponse)
async def scanner_page(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    ctx = await _base_context(request)
    ctx.update({"report": None, "verdict": None})
    return templates.TemplateResponse("clean_scanner.html", ctx)


@app.post("/scanner/check", response_class=HTMLResponse)
async def scanner_check(
    request: Request,
    source: str = Form(""),
    link: str = Form(""),
    file: UploadFile | None = File(None),
):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    try:
        text = source or ""
        if file and file.filename:
            text = (await file.read()).decode("utf-8", errors="replace")
        if link and not text.strip():
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=20) as response:
                    text = await response.text()
        if not text.strip():
            raise RuntimeError("Нечего проверять: вставь ссылку, файл или код")
        report = scan_module_source(text)
        verdict = "ALLOWED" if report.allowed else "BLOCKED"
        ctx = await _base_context(request)
        ctx.update({"report": report, "verdict": verdict})
        return templates.TemplateResponse("clean_scanner.html", ctx)
    except Exception as exc:
        return RedirectResponse(f"/scanner?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/update")
async def update_project(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            timeout=60,
        )
        output = (result.stdout + "\n" + result.stderr).strip() or "No output"
        return RedirectResponse(f"/?message={output[-240:]}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)
