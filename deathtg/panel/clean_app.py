from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
import re

import aiohttp
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from deathtg.config import ROOT_DIR, RUNTIME_DIR
from deathtg.metrics import init_metrics
from deathtg.panel.auth_flow import begin_login, confirm_2fa, confirm_code, finish_login, write_env
from deathtg.panel.clean_actions import load_pending_install, router as actions_router
from deathtg.panel.clean_core import (
    STATIC_DIR,
    activity_points,
    env_load,
    has_env,
    has_session,
    load_module_meta,
    loader,
    module_repo,
    module_detail,
    repo_module_detail,
    panel_password,
    profile_info,
    refresh_modules,
    registry,
    startup_status,
    status,
    templates,
    top_modules,
)
from deathtg.panel.re_auth import router as reconnect_router
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import is_trusted_module_link, scan_module_source


env_load()
PANEL_GRANTS_PATH = RUNTIME_DIR / "panel_grants.json"
PANEL_GRANT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

app = FastAPI(title="DeathTG Panel")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("PANEL_SECRET", secrets.token_hex(32)),
    same_site="strict",
    https_only=False,
    max_age=60 * 60 * 24 * 90,
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


def _load_panel_grants() -> dict:
    if not PANEL_GRANTS_PATH.exists():
        return {}
    try:
        data = json.loads(PANEL_GRANTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_panel_grants(data: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PANEL_GRANTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _base_context(request: Request) -> dict:
    profile = await profile_info()
    st = await status(profile)
    return {
        "request": request,
        "profile": profile,
        "status": st,
        "startup": startup_status(),
        "module_meta": load_module_meta(),
        "pending_warning": load_pending_install(request.query_params.get("warning")),
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    }


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if has_env() and has_session() and request.session.get("auth"):
        return RedirectResponse("/", status_code=303)
    if has_env() and has_session():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request, "step": "start", "error": None})


@app.post("/setup/save")
async def setup_save(
    request: Request,
    api_id: int = Form(...),
    api_hash: str = Form(...),
    phone: str = Form(...),
    session_name: str = Form("deathtg"),
    panel_password_value: str = Form("deathtg"),
    panel_secret: str = Form("change_me_long_secret"),
    bot_token: str = Form(""),
):
    try:
        panel_key = (panel_password_value or "").strip() or "deathtg"
        secret_value = (panel_secret or "").strip() or secrets.token_urlsafe(32)
        write_env(api_id, api_hash, session_name, phone, panel_key, secret_value, bot_token)
        os.environ["PANEL_PASSWORD"] = panel_key
        os.environ["PANEL_SECRET"] = secret_value
        panel_password.cache_clear()
        flow_id = secrets.token_urlsafe(16)
        request.session["setup_flow_id"] = flow_id
        await begin_login(flow_id, api_id, api_hash, phone, session_name)
        return templates.TemplateResponse("setup.html", {"request": request, "step": "pin", "error": None})
    except Exception as exc:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "start", "error": f"{type(exc).__name__}: {exc}"},
        )


@app.post("/setup/pin")
async def setup_pin(request: Request, pin: str = Form(...)):
    flow_id = request.session.get("setup_flow_id")
    if not flow_id:
        return RedirectResponse("/setup", status_code=303)
    try:
        state = await confirm_code(flow_id, pin)
        if state == "2fa":
            return templates.TemplateResponse("setup.html", {"request": request, "step": "secret", "error": None})
        await finish_login(flow_id)
        request.session.pop("setup_flow_id", None)
        request.session["auth"] = True
        return RedirectResponse("/?message=Telegram+connected.+Userbot+will+start+automatically", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "pin", "error": f"{type(exc).__name__}: {exc}"},
        )


@app.post("/setup/secret")
async def setup_secret(request: Request, secret_value: str = Form(...)):
    flow_id = request.session.get("setup_flow_id")
    if not flow_id:
        return RedirectResponse("/setup", status_code=303)
    try:
        await confirm_2fa(flow_id, secret_value)
        await finish_login(flow_id)
        request.session.pop("setup_flow_id", None)
        request.session["auth"] = True
        return RedirectResponse("/?message=Telegram+connected.+Userbot+will+start+automatically", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "secret", "error": f"{type(exc).__name__}: {exc}"},
        )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(
        "clean_login.html",
        {"request": request, "error": request.query_params.get("error")},
    )


@app.get("/grant/{token}")
async def grant_login(request: Request, token: str):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    if not PANEL_GRANT_TOKEN_RE.fullmatch(token or ""):
        return RedirectResponse("/login?error=Invalid+grant+token", status_code=303)
    data = _load_panel_grants()
    entry = data.get(token)
    if not isinstance(entry, dict):
        return RedirectResponse("/login?error=Grant+token+not+found", status_code=303)
    now = int(time.time())
    expires_at = int(entry.get("expires_at", 0) or 0)
    if bool(entry.get("used")):
        return RedirectResponse("/login?error=Grant+token+already+used", status_code=303)
    if expires_at and expires_at < now:
        return RedirectResponse("/login?error=Grant+token+expired", status_code=303)
    entry["used"] = True
    entry["used_at"] = now
    data[token] = entry
    _save_panel_grants(data)
    request.session["auth"] = True
    return RedirectResponse("/?message=Connected+from+bot+link", status_code=303)


@app.post("/login")
async def login(request: Request, key: str = Form(...)):
    if secrets.compare_digest(key, panel_password()):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("clean_login.html", {"request": request, "error": "Invalid panel password"})


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
    ctx = await _base_context(request)
    ctx["top_modules"] = await top_modules()
    return templates.TemplateResponse("clean_profile.html", ctx)


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


@app.get("/repo-modules/{name}", response_class=HTMLResponse)
async def repo_module_detail_page(request: Request, name: str):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    ctx = await _base_context(request)
    ctx.update({"module": await repo_module_detail(name), "protected": PROTECTED_MODULES})
    return templates.TemplateResponse("clean_module_detail.html", ctx)


@app.get("/modules/{name}", response_class=HTMLResponse)
async def module_detail_page(request: Request, name: str):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    await refresh_modules()
    ctx = await _base_context(request)
    ctx.update({"module": await module_detail(name), "protected": PROTECTED_MODULES})
    return templates.TemplateResponse("clean_module_detail.html", ctx)


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
            url = loader._normalize_github_url(link)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=20) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Download failed, HTTP {response.status}")
                    text = await response.text()
            if loader._looks_like_html(text):
                raise RuntimeError("URL returned HTML, not Python code. Use raw/blob .py link.")
        if not text.strip():
            raise RuntimeError("Nothing to scan. Paste code, upload a file, or provide a module link.")
        trusted = is_trusted_module_link(link) if link else False
        report = scan_module_source(text, trusted=trusted)
        verdict = report.verdict
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
