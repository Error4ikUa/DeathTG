from __future__ import annotations

"""
This file contains the entrypoint for the web panel.  It replaces the
deprecated ``pages.py`` and ``server_v2.py`` modules and uses FastAPI to
serve the UI.  The panel exposes routes for the home page, profile page,
module browser and installed modules list.  All routes are asynchronous
and await the underlying metrics and profile functions to avoid 500
errors caused by returning unawaited coroutines to Jinja2.

In addition to wiring up routes, the application registers a startup
event that initialises the metrics database.  Without this hook
``init_metrics`` would be invoked lazily on the first metric call,
potentially causing a race when multiple requests arrive at once.  By
pre‑creating the tables on startup we guarantee that later calls to
``usage_total`` or ``installed_days`` complete without locking.
"""

import os
import secrets
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from deathtg.panel.clean_core import (
    STATIC_DIR,
    profile_info,
    status,
    templates,
    env_load,
    has_env,
    has_session,
    activity_points,
    top_modules,
    module_repo,
    registry,
)
from deathtg.panel.clean_actions import router as actions_router
from deathtg.registry import PROTECTED_MODULES
from deathtg.metrics import init_metrics


# Ensure environment variables are loaded from ``.env``.  This call must
# happen before instantiating the FastAPI app so that settings are
# available when the app starts up.
env_load()

app = FastAPI()

# Use a random session secret for the development server.  In a
# production deployment this key should be set via environment
# variables or another secret manager.
app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(32))

# Mount static files for CSS/JS/assets.  ``STATIC_DIR`` points into the
# ``deathtg/panel/static`` directory of the main project.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Register action handlers (form submissions, uploads, etc.).
app.include_router(actions_router)


@app.on_event("startup")
async def startup_event() -> None:
    """Initialise the metrics database on application startup.

    Without this call the metrics tables are created lazily on the
    first access.  In a multi‑threaded environment that can result in
    race conditions where two requests attempt to create the tables at
    the same time.  Pre‑creating the tables on startup avoids this
    situation.
    """
    try:
        await init_metrics()
    except Exception:
        # If metrics initialisation fails we still continue; later
        # accesses will attempt to initialise again.
        pass


@app.get("/")
async def home(request: Request):
    """Render the dashboard home page.

    The home page shows basic status information and the user's
    profile.  If the environment has not yet been configured the
    request is redirected to the setup page.  If the user is not
    authenticated they are redirected to the login page.
    """
    if not has_env():
        return RedirectResponse("/setup")
    if not request.session.get("auth"):
        return RedirectResponse("/login")
    profile = await profile_info()
    st = await status(profile)
    return templates.TemplateResponse(
        "clean_home.html", {"request": request, "profile": profile, "status": st, "page": "home"}
    )


@app.get("/profile")
async def profile_page(request: Request):
    """Render the profile page.

    Displays details from ``profile_info`` and the current status.  If
    the visitor is not authenticated they are redirected to the login
    page.
    """
    if not request.session.get("auth"):
        return RedirectResponse("/login")
    profile = await profile_info()
    st = await status(profile)
    return templates.TemplateResponse(
        "clean_profile.html", {"request": request, "profile": profile, "status": st}
    )


@app.get("/browser")
async def browser_page(request: Request):
    """Render the module browser page.

    The browser page lists available modules from the remote
    repository.  It also displays the user's profile and current
    status.
    """
    profile = await profile_info()
    st = await status(profile)
    repo = await module_repo()
    return templates.TemplateResponse(
        "clean_browser.html", {"request": request, "profile": profile, "status": st, "browser_modules": repo}
    )


@app.get("/installed")
async def installed_page(request: Request):
    """Render the installed modules page.

    Shows modules currently loaded into the bot along with basic
    statistics about the installation.  Protected modules (those that
    cannot be uninstalled) are passed separately to the template.
    """
    profile = await profile_info()
    st = await status(profile)
    return templates.TemplateResponse(
        "clean_installed.html",
        {
            "request": request,
            "profile": profile,
            "status": st,
            "grouped": registry.by_module(),
            "protected": PROTECTED_MODULES,
        },
    )