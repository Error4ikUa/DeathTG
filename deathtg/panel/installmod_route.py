from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from deathtg.panel.clean_core import has_env, has_session, templates

router = APIRouter()


def gate(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    if not has_session():
        return RedirectResponse("/reconnect", status_code=303)
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return None


@router.get("/install")
async def legacy_install():
    return RedirectResponse("/installmod", status_code=303)


@router.get("/installmod")
async def installmod(request: Request):
    blocked = gate(request)
    if blocked:
        return blocked
    from deathtg.panel.clean_app import base
    return templates.TemplateResponse("clean_install.html", await base(request, "installmod"))
