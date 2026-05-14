from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.panel.clean_core import (
    STATIC_DIR,
    activity_points,
    env_load,
    has_env,
    has_session,
    loader,
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

env_load()

app = FastAPI(title="DeathTG Clean Panel")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("PANEL_SECRET", secrets.token_hex(32)), same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(reconnect_router)


def guard(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    if not has_session():
        return RedirectResponse("/reconnect", status_code=303)
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return None


async def base(request: Request, page: str):
    await refresh_modules()
    profile = await profile_info()
    return {
        "request": request,
        "page": page,
        "profile": profile,
        "status": status(profile),
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("clean_login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, key: str = Form(...)):
    if secrets.compare_digest(key.strip(), panel_password().strip()):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("clean_login.html", {"request": request, "error": "Неверный пароль панели"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    locked = guard(request)
    if locked: return locked
    ctx = await base(request, "home")
    return templates.TemplateResponse("clean_home.html", ctx)


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request):
    locked = guard(request)
    if locked: return locked
    ctx = await base(request, "profile")
    return templates.TemplateResponse("clean_profile.html", ctx)


@app.get("/activity", response_class=HTMLResponse)
async def activity(request: Request):
    locked = guard(request)
    if locked: return locked
    ctx = await base(request, "activity")
    ctx.update({"activity_points": activity_points(), "top_modules": top_modules()})
    return templates.TemplateResponse("clean_activity.html", ctx)


@app.get("/browser", response_class=HTMLResponse)
async def browser(request: Request):
    locked = guard(request)
    if locked: return locked
    ctx = await base(request, "browser")
    ctx.update({"browser_modules": await module_repo()})
    return templates.TemplateResponse("clean_browser.html", ctx)


@app.get("/installed", response_class=HTMLResponse)
async def installed(request: Request):
    locked = guard(request)
    if locked: return locked
    ctx = await base(request, "installed")
    ctx.update({"grouped": registry.by_module(), "protected": PROTECTED_MODULES})
    return templates.TemplateResponse("clean_installed.html", ctx)


@app.get("/install", response_class=HTMLResponse)
async def install(request: Request):
    locked = guard(request)
    if locked: return locked
    ctx = await base(request, "install")
    return templates.TemplateResponse("clean_install.html", ctx)


@app.get("/scanner", response_class=HTMLResponse)
async def scanner(request: Request):
    locked = guard(request)
    if locked: return locked
    ctx = await base(request, "scanner")
    return templates.TemplateResponse("clean_scanner.html", ctx)
