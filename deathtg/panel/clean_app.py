from __future__ import annotations

import ipaddress
import os
import secrets
import time
import re

import aiohttp
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from deathtg.assets import IMAGES_DIR, module_image_path
from deathtg.metrics import init_metrics
from deathtg.panel.auth_flow import begin_login, confirm_2fa, confirm_code, finish_login, friendly_login_error, login_hint, resend_code, write_env
from deathtg.panel.clean_actions import load_pending_install, router as actions_router
from deathtg.panel.clean_core import (
    STATIC_DIR,
    activity_points,
    env_load,
    has_env,
    has_session,
    installed_module_cards,
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
from deathtg.panel_access import (
    active_device,
    consume_device_grant,
    friendly_device_name,
    issue_device_grant,
    list_devices,
    public_panel_enabled,
    remember_device_session,
    revoke_device_session,
    touch_device_session,
)
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import is_trusted_module_link, scan_module_source
from deathtg.server_bootstrap import (
    ensure_server_env,
    panel_allowed_hosts,
    panel_cookie_secure,
    panel_trust_proxy,
    secure_panel_password,
    secure_panel_secret,
)
from deathtg.setup_access import current_setup_token, valid_setup_token
from deathtg.update_manager import apply_update, inspect_update, load_update_state, save_update_state, schedule_restart


env_load()
ensure_server_env()
PANEL_GRANT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{16,512}$")
AUTH_WINDOW_SECONDS = 10 * 60
AUTH_ATTEMPT_LIMITS = {"login": 8, "setup_save": 6, "setup_pin": 10, "setup_secret": 8}
AUTH_ATTEMPTS: dict[tuple[str, str], list[float]] = {}
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

app = FastAPI(title="DeathTG Panel")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("PANEL_SECRET", secrets.token_hex(32)),
    same_site="strict",
    https_only=panel_cookie_secure(),
    max_age=60 * 60 * 24 * 90,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=panel_allowed_hosts())
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")
app.include_router(actions_router)
app.include_router(reconnect_router)


@app.on_event("startup")
async def startup_event() -> None:
    await init_metrics()
    await refresh_modules()


@app.middleware("http")
async def harden_responses(request: Request, call_next):
    session_data = request.scope.get("session") or {}
    session_id = str(session_data.get("device_session_id") or "")
    if session_id:
        touch_device_session(session_id, ip=_client_ip(request), user_agent=request.headers.get("user-agent", ""))
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if request.url.path.startswith(("/login", "/setup", "/grant", "/reconnect")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _auth_guard(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    session_id = str(request.session.get("device_session_id") or "")
    if session_id and not active_device(session_id):
        request.session.clear()
        return RedirectResponse("/login?error=Device+session+revoked", status_code=303)
    return None


def _client_ip(request: Request) -> str:
    if panel_trust_proxy():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return getattr(getattr(request, "client", None), "host", None) or "unknown"


def _is_local_request(request: Request) -> bool:
    try:
        return ipaddress.ip_address(_client_ip(request)).is_loopback
    except Exception:
        return False


def _is_rate_limited(bucket: str, request: Request) -> bool:
    key = (bucket, _client_ip(request))
    now = time.time()
    attempts = [stamp for stamp in AUTH_ATTEMPTS.get(key, []) if now - stamp <= AUTH_WINDOW_SECONDS]
    AUTH_ATTEMPTS[key] = attempts
    return len(attempts) >= AUTH_ATTEMPT_LIMITS.get(bucket, 8)


def _mark_auth_failure(bucket: str, request: Request) -> None:
    key = (bucket, _client_ip(request))
    now = time.time()
    attempts = [stamp for stamp in AUTH_ATTEMPTS.get(key, []) if now - stamp <= AUTH_WINDOW_SECONDS]
    attempts.append(now)
    AUTH_ATTEMPTS[key] = attempts


def _clear_auth_failures(bucket: str, request: Request) -> None:
    AUTH_ATTEMPTS.pop((bucket, _client_ip(request)), None)


def _setup_allowed(request: Request, setup_token: str = "") -> bool:
    if _is_local_request(request):
        return True
    return valid_setup_token(setup_token or request.query_params.get("setup_token", ""))


async def _base_context(request: Request) -> dict:
    profile = await profile_info()
    st = await status(profile)
    session_id = str(request.session.get("device_session_id") or "")
    return {
        "request": request,
        "profile": profile,
        "status": st,
        "startup": startup_status(),
        "devices": list_devices(),
        "current_device": active_device(session_id) if session_id else None,
        "public_panel_enabled": public_panel_enabled(),
        "device_link": request.session.pop("fresh_device_link", None),
        "update_info": load_update_state(),
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
    if not _setup_allowed(request):
        return HTMLResponse(
            "<html><body style='background:#050b08;color:#eaffef;font-family:sans-serif;padding:40px'>"
            "<h1>Setup token required</h1>"
            "<p>Open the setup link printed in the server console.</p>"
            "</body></html>",
            status_code=403,
        )
    return templates.TemplateResponse(
        "setup.html",
        {"request": request, "step": "start", "error": None, "setup_token": current_setup_token()},
    )


@app.get("/setup/done", response_class=HTMLResponse)
async def setup_done_page(request: Request):
    local_ready = bool(request.session.get("auth"))
    body = (
        "<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>DeathTG Ready</title></head>"
        "<body style='margin:0;min-height:100vh;display:grid;place-items:center;background:#040a07;color:#eaffef;font-family:sans-serif;padding:24px'>"
        "<div style='max-width:680px;padding:28px;border:1px solid rgba(82,255,139,.24);border-radius:22px;background:rgba(4,18,11,.92)'>"
        "<h1 style='margin-top:0'>Telegram connected</h1>"
        "<p>DeathTG created your session and is preparing secure access.</p>"
        "<p>Check Telegram. Your personal secure panel links will arrive from the DeathTG bot.</p>"
        "<p>Do not share those links with anyone.</p>"
        "<p>If the bot message does not appear yet, wait a little and refresh later.</p>"
    )
    if local_ready:
        body += "<p><a href='/' style='color:#52ff8b'>Open panel in this browser</a></p>"
    body += "</div></body></html>"
    return HTMLResponse(body)


@app.post("/setup/save")
async def setup_save(
    request: Request,
    api_id: int = Form(...),
    api_hash: str = Form(...),
    phone: str = Form(...),
    session_name: str = Form("deathtg"),
    panel_password_value: str = Form(""),
    panel_secret: str = Form(""),
    bot_token: str = Form(""),
    setup_token: str = Form(""),
):
    if not _setup_allowed(request, setup_token):
        return HTMLResponse(
            "<html><body style='background:#050b08;color:#eaffef;font-family:sans-serif;padding:40px'>"
            "<h1>Setup token required</h1>"
            "<p>Open the setup link printed in the server console.</p>"
            "</body></html>",
            status_code=403,
        )
    if _is_rate_limited("setup_save", request):
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "step": "start",
                "error": "Too many setup attempts. Wait a few minutes and try again.",
                "setup_token": current_setup_token(),
            },
            status_code=429,
        )
    try:
        panel_key = secure_panel_password(panel_password_value)
        secret_value = secure_panel_secret(panel_secret)
        write_env(api_id, api_hash, session_name, phone, panel_key, secret_value, bot_token)
        os.environ["PANEL_PASSWORD"] = panel_key
        os.environ["PANEL_SECRET"] = secret_value
        panel_password.cache_clear()
        flow_id = secrets.token_urlsafe(16)
        request.session["setup_flow_id"] = flow_id
        login_state = await begin_login(flow_id, api_id, api_hash, phone, session_name)
        if login_state == "authorized":
            await finish_login(flow_id)
            request.session.pop("setup_flow_id", None)
            request.session["auth"] = True
            session_id = secrets.token_urlsafe(18)
            request.session["device_session_id"] = session_id
            remember_device_session(
                session_id,
                ip=_client_ip(request),
                user_agent=request.headers.get("user-agent", ""),
                label=friendly_device_name(request.headers.get("user-agent", ""), "Setup device"),
                auth_method="setup",
            )
            _clear_auth_failures("setup_save", request)
            return RedirectResponse("/setup/done", status_code=303)
        _clear_auth_failures("setup_save", request)
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "step": "pin",
                "error": None,
                **login_hint(flow_id),
            },
        )
    except Exception as exc:
        _mark_auth_failure("setup_save", request)
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "start", "error": friendly_login_error(exc), "setup_token": current_setup_token()},
        )


@app.post("/setup/pin")
async def setup_pin(request: Request, pin: str = Form(...)):
    flow_id = request.session.get("setup_flow_id")
    if not flow_id:
        return RedirectResponse("/setup", status_code=303)
    if _is_rate_limited("setup_pin", request):
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "pin", "error": "Too many code attempts. Request a new code and try again."},
            status_code=429,
        )
    try:
        state = await confirm_code(flow_id, pin)
        if state == "2fa":
            _clear_auth_failures("setup_pin", request)
            return templates.TemplateResponse("setup.html", {"request": request, "step": "secret", "error": None})
        await finish_login(flow_id)
        request.session.pop("setup_flow_id", None)
        request.session["auth"] = True
        session_id = secrets.token_urlsafe(18)
        request.session["device_session_id"] = session_id
        remember_device_session(
            session_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
            label=friendly_device_name(request.headers.get("user-agent", ""), "Setup device"),
            auth_method="setup",
        )
        _clear_auth_failures("setup_pin", request)
        return RedirectResponse("/setup/done", status_code=303)
    except Exception as exc:
        _mark_auth_failure("setup_pin", request)
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "step": "pin",
                "error": friendly_login_error(exc),
                **login_hint(flow_id),
            },
        )


@app.post("/setup/secret")
async def setup_secret(request: Request, secret_value: str = Form(...)):
    flow_id = request.session.get("setup_flow_id")
    if not flow_id:
        return RedirectResponse("/setup", status_code=303)
    if _is_rate_limited("setup_secret", request):
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "secret", "error": "Too many 2FA attempts. Wait a few minutes and try again."},
            status_code=429,
        )
    try:
        await confirm_2fa(flow_id, secret_value)
        await finish_login(flow_id)
        request.session.pop("setup_flow_id", None)
        request.session["auth"] = True
        session_id = secrets.token_urlsafe(18)
        request.session["device_session_id"] = session_id
        remember_device_session(
            session_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
            label=friendly_device_name(request.headers.get("user-agent", ""), "Setup device"),
            auth_method="setup",
        )
        _clear_auth_failures("setup_secret", request)
        return RedirectResponse("/setup/done", status_code=303)
    except Exception as exc:
        _mark_auth_failure("setup_secret", request)
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "step": "secret", "error": f"{type(exc).__name__}: {exc}"},
        )


@app.post("/setup/resend")
async def setup_resend(request: Request):
    flow_id = request.session.get("setup_flow_id")
    if not flow_id:
        return RedirectResponse("/setup", status_code=303)
    try:
        hint = await resend_code(flow_id)
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "step": "pin",
                "error": None,
                "message": "A fresh Telegram login code was requested for DeathTG setup.",
                **hint,
            },
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "step": "pin",
                "error": friendly_login_error(exc),
                **login_hint(flow_id),
            },
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
    try:
        payload = consume_device_grant(
            token,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    except Exception as exc:
        return RedirectResponse(f"/login?error={type(exc).__name__}: {exc}", status_code=303)
    request.session["auth"] = True
    request.session["device_session_id"] = payload["session_id"]
    return RedirectResponse("/?message=Connected+from+secure+device+link", status_code=303)


@app.post("/login")
async def login(request: Request, key: str = Form(...)):
    if _is_rate_limited("login", request):
        return templates.TemplateResponse(
            "clean_login.html",
            {"request": request, "error": "Too many login attempts. Wait a few minutes and try again."},
            status_code=429,
        )
    if public_panel_enabled() and not _is_local_request(request):
        return templates.TemplateResponse(
            "clean_login.html",
            {"request": request, "error": "Remote password login is disabled. Use a secure device link from Telegram or from an already trusted device."},
            status_code=403,
        )
    if secrets.compare_digest(key, panel_password()):
        request.session["auth"] = True
        session_id = str(request.session.get("device_session_id") or "") or secrets.token_urlsafe(18)
        request.session["device_session_id"] = session_id
        remember_device_session(
            session_id,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
            label=friendly_device_name(request.headers.get("user-agent", ""), "Browser"),
            auth_method="password",
        )
        _clear_auth_failures("login", request)
        return RedirectResponse("/", status_code=303)
    _mark_auth_failure("login", request)
    return templates.TemplateResponse("clean_login.html", {"request": request, "error": "Invalid panel password"})


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True, "panel": "up", "session_file": has_session(), "env": has_env()})


@app.get("/module-media/{name}")
async def module_media(name: str):
    path = module_image_path(name)
    if not path or not path.exists():
        return JSONResponse({"error": "module image not found"}, status_code=404)
    return FileResponse(path)


@app.post("/devices/link")
async def create_device_link(request: Request, device_name: str = Form("")):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    label = (device_name or "").strip() or friendly_device_name(request.headers.get("user-agent", ""), "New device")
    request.session["fresh_device_link"] = issue_device_grant(label, created_by="panel")
    return RedirectResponse("/profile?message=Secure+device+link+created", status_code=303)


@app.post("/devices/{session_id}/revoke")
async def revoke_device(request: Request, session_id: str):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    revoke_device_session(session_id)
    current_session_id = str(request.session.get("device_session_id") or "")
    if current_session_id == session_id:
        request.session.clear()
        return RedirectResponse("/login?message=Current+device+revoked", status_code=303)
    return RedirectResponse("/profile?message=Device+revoked", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    session_id = str(request.session.get("device_session_id") or "")
    if session_id:
        revoke_device_session(session_id)
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
            "module_cards": installed_module_cards(registry.by_module()),
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
    grouped = registry.by_module()
    ctx.update({"grouped": grouped, "module_cards": installed_module_cards(grouped), "protected": PROTECTED_MODULES})
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


@app.post("/system/update/check")
async def check_update(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    try:
        info = inspect_update()
        save_update_state(info)
        if not info.get("ok"):
            return RedirectResponse(f"/profile?error={str(info.get('message') or 'Update check failed')}", status_code=303)
        if info.get("update_available"):
            return RedirectResponse("/profile?message=Update+available", status_code=303)
        return RedirectResponse("/profile?message=Already+up+to+date", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/profile?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/system/update/apply")
async def update_project(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    try:
        result = apply_update()
        save_update_state(result)
        if not result.get("ok"):
            return RedirectResponse(f"/profile?error={str(result.get('message') or 'Update failed')}", status_code=303)
        if result.get("updated"):
            return RedirectResponse("/profile?message=Update+installed.+Restart+to+apply", status_code=303)
        return RedirectResponse("/profile?message=Already+up+to+date", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/profile?error={type(exc).__name__}: {exc}", status_code=303)


@app.post("/system/restart")
async def restart_project(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    schedule_restart()
    return HTMLResponse(
        "<html><head><meta http-equiv='refresh' content='8;url=/'></head>"
        "<body style='background:#050b08;color:#eaffef;font-family:sans-serif;padding:40px'>"
        "<h1>DeathTG is restarting...</h1><p>Wait a few seconds, then this page will try to reopen the panel.</p>"
        "</body></html>"
    )
