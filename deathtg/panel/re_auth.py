from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from deathtg.config import ROOT_DIR, load_config
from deathtg.panel.auth_flow import begin_login, confirm_code, confirm_2fa, finish_login

router = APIRouter()
templates = Jinja2Templates(directory=ROOT_DIR / "deathtg" / "panel" / "templates")


def _phone() -> str:
    env = ROOT_DIR / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("PHONE="):
            return line.split("=", 1)[1].strip()
    return ""


@router.get("/reconnect")
async def page(request: Request):
    return templates.TemplateResponse("reconnect.html", {"request": request, "step": "start", "error": None})


@router.post("/reconnect/start")
async def start(request: Request):
    try:
        cfg = load_config()
        phone = _phone()
        if not phone:
            raise RuntimeError("PHONE not found in .env")
        flow_id = secrets.token_urlsafe(16)
        request.session["flow_id"] = flow_id
        await begin_login(flow_id, cfg.api_id, cfg.api_hash, phone, cfg.session_name)
        return templates.TemplateResponse("reconnect.html", {"request": request, "step": "pin", "error": None})
    except Exception as exc:
        return templates.TemplateResponse("reconnect.html", {"request": request, "step": "start", "error": f"{type(exc).__name__}: {exc}"})


@router.post("/reconnect/pin")
async def pin(request: Request, pin: str = Form(...)):
    try:
        flow_id = request.session["flow_id"]
        state = await confirm_code(flow_id, pin)
        if state == "2fa":
            return templates.TemplateResponse("reconnect.html", {"request": request, "step": "secret", "error": None})
        await finish_login(flow_id)
        request.session["auth"] = True
        return RedirectResponse("/?message=Session recreated", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("reconnect.html", {"request": request, "step": "pin", "error": f"{type(exc).__name__}: {exc}"})


@router.post("/reconnect/secret")
async def secret(request: Request, secret_value: str = Form(...)):
    try:
        flow_id = request.session["flow_id"]
        await confirm_2fa(flow_id, secret_value)
        await finish_login(flow_id)
        request.session["auth"] = True
        return RedirectResponse("/?message=Session recreated", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse("reconnect.html", {"request": request, "step": "secret", "error": f"{type(exc).__name__}: {exc}"})
