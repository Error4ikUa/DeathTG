from __future__ import annotations

"""
Action handlers for the DeathTG control panel.

These endpoints handle form submissions and file uploads from the
browser interface.  Each handler verifies that the user is
authenticated via the session before performing its action.  All
operations interacting with the module loader are awaited to ensure
that the asynchronous loader methods complete before a redirect is
issued.
"""

import base64
import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.panel.clean_core import loader, registry
from deathtg.profile_store import save_profile_settings, update_env_value
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import scan_module_source


router = APIRouter()

# Directory where user‑uploaded assets (like avatars) are stored.
USER_STATIC_DIR = Path(__file__).resolve().parent / "static" / "user"


@router.post("/profile/save")
async def save_profile(
    request: Request,
    profile_title: str = Form(""),
    description: str = Form(""),
    accent: str = Form("blue"),
    command_prefix: str = Form("."),
):
    """Update profile settings and redirect back to the profile page.

    The command prefix is stored in the environment so that the bot uses
    the new prefix immediately.  Only authenticated sessions may
    update settings.
    """
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    save_profile_settings(profile_title=profile_title, description=description, accent=accent)
    if command_prefix:
        update_env_value("COMMAND_PREFIX", command_prefix)
    return RedirectResponse("/profile?message=Saved", status_code=303)


@router.post("/profile/avatar")
async def upload_avatar(request: Request, avatar_base64: str = Form(...)):
    """Accept a base64‑encoded avatar image and save it to disk.

    The client sends an image encoded as a data URI; this handler
    decodes the image and writes it to ``static/user/avatar.png``.  The
    returned JSON includes a timestamp parameter in the URL to bust
    browser caches.
    """
    if not request.session.get("auth"):
        return JSONResponse({"error": "Auth"}, 401)
    try:
        data = base64.b64decode(avatar_base64.split(",", 1)[1])
        USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
        (USER_STATIC_DIR / "avatar.png").write_bytes(data)
        return JSONResponse({"status": "ok", "avatar": f"/static/user/avatar.png?t={int(time.time())}"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


@router.post("/modules/download")
async def download_mod(request: Request, link: str = Form(...)):
    """Download and load an external module from a provided URL.

    The loader verifies the URL and code before saving and importing
    the module.  On success the user is redirected back to the module
    browser page with a success message.
    """
    try:
        path = await loader.download_module(link)
        name = await loader.load_file(path)
        return RedirectResponse(f"/browser?message=Installed: {name}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/browser?error={str(e)}", status_code=303)


@router.post("/modules/upload")
async def upload_mod(request: Request, file: UploadFile = File(...)):
    """Upload a local module file and load it into the bot.

    The uploaded file is saved into the ``modules`` directory and then
    imported via the loader.  Errors are captured and displayed to
    the user.
    """
    try:
        target = MODULES_DIR / file.filename
        content = await file.read()
        target.write_bytes(content)
        name = await loader.load_file(target)
        return RedirectResponse(f"/installed?message=Uploaded: {name}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/installed?error={str(e)}", status_code=303)


@router.post("/modules/{name}/delete")
async def delete_mod(request: Request, name: str):
    """Unload and delete a previously loaded module.

    Protected modules (those essential to the system) cannot be
    removed; attempts to delete them raise a ``RuntimeError``.
    """
    try:
        if name in PROTECTED_MODULES:
            raise RuntimeError("Protected")
        loader.unload(name, silent=True)
        (MODULES_DIR / f"{name}.py").unlink(missing_ok=True)
        return RedirectResponse("/installed?message=Deleted", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/installed?error={str(e)}", status_code=303)