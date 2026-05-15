from __future__ import annotations
import os, secrets
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from deathtg.panel.clean_core import STATIC_DIR, profile_info, status, templates, env_load, has_env, has_session, activity_points, top_modules, module_repo, registry
from deathtg.panel.clean_actions import router as actions_router
from deathtg.registry import PROTECTED_MODULES

env_load()
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(32))
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(actions_router) # КРИТИЧНО: подключаем действия

@app.get("/")
async def home(request: Request):
    if not has_env(): return RedirectResponse("/setup")
    if not request.session.get("auth"): return RedirectResponse("/login")
    profile = await profile_info()
    st = await status(profile)
    return templates.TemplateResponse("clean_home.html", {"request": request, "profile": profile, "status": st, "page": "home"})

@app.get("/profile")
async def profile_page(request: Request):
    if not request.session.get("auth"): return RedirectResponse("/login")
    profile = await profile_info()
    st = await status(profile)
    return templates.TemplateResponse("clean_profile.html", {"request": request, "profile": profile, "status": st})

@app.get("/browser")
async def browser_page(request: Request):
    profile = await profile_info()
    st = await status(profile)
    repo = await module_repo()
    return templates.TemplateResponse("clean_browser.html", {"request": request, "profile": profile, "status": st, "browser_modules": repo})

@app.get("/installed")
async def installed_page(request: Request):
    profile = await profile_info()
    st = await status(profile)
    return templates.TemplateResponse("clean_installed.html", {"request": request, "profile": profile, "status": st, "grouped": registry.by_module(), "protected": PROTECTED_MODULES})
