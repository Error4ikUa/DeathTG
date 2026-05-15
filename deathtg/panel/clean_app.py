from __future__ import annotations

import os
import secrets

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from deathtg.config import ROOT_DIR
from deathtg.panel.clean_actions import router as actions_router
from deathtg.panel.clean_core import (
    STATIC_DIR,
    activity_points,
    env_load,
    has_env,
    has_session,
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

env_load()

app = FastAPI(title="DeathTG Clean Panel")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("PANEL_SECRET", secrets.token_hex(32)), same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(reconnect_router)
app.include_router(actions_router)


def guard(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    if not has_session():
        return RedirectResponse("/reconnect", status_code=303)
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return None


def static_file(name: str):
    target = STATIC_DIR / name
    if target.exists() and target.is_file():
        return FileResponse(target)
    return RedirectResponse(f"/static/{name}", status_code=307)


@app.get("/theme_cards.css")
async def theme_cards_css():
    return static_file("theme_cards.css")


def write_env(api_id: int, api_hash: str, session_name: str, phone: str, panel_secret: str, bot_token: str = "") -> str:
    key = secrets.token_urlsafe(18)
    text = (
        f"API_ID={api_id}\n"
        f"API_HASH={api_hash}\n"
        f"SESSION_NAME={session_name}\n"
        "COMMAND_PREFIX=.\n"
        "OWNER_ID=\n"
        f"BOT_TOKEN={bot_token}\n"
        f"PANEL_PASSWORD={key}\n"
        f"PANEL_SECRET={panel_secret}\n"
        f"PHONE={phone}\n"
    )
    (ROOT_DIR / ".env").write_text(text, encoding="utf-8")
    os.environ["PANEL_PASSWORD"] = key
    os.environ["PANEL_SECRET"] = panel_secret
    return key


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
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
        key = write_env(api_id, api_hash, session_name, phone, panel_secret, bot_token)
        request.session["auth"] = True
        return RedirectResponse(f"/reconnect?message=Config saved. Panel password: {key}", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("setup.html", {"request": request, "step": "start", "error": f"{type(exc).__name__}: {exc}"})


async def base(request: Request, page: str):
    await refresh_modules()
    profile = await profile_info()
    return {"request": request, "page": page, "profile": profile, "status": status(profile), "message": request.query_params.get("message"), "error": request.query_params.get("error")}


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
    return templates.TemplateResponse("clean_home.html", await base(request, "home"))


@app.get("/profile", response_class=HTMLResponse)
async def profile(request: Request):
    locked = guard(request)
    if locked: return locked
    return templates.TemplateResponse("clean_profile.html", await base(request, "profile"))


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
    ctx.update({"browser_modules": await module_repo(), "grouped": registry.by_module(), "protected": PROTECTED_MODULES})
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
    return templates.TemplateResponse("clean_install.html", await base(request, "install"))


@app.get("/scanner", response_class=HTMLResponse)
async def scanner(request: Request):
    locked = guard(request)
    if locked: return locked
    return templates.TemplateResponse("clean_scanner.html", await base(request, "scanner"))
