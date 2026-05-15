from __future__ import annotations
import base64, os, subprocess, time
from pathlib import Path
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.panel.clean_core import loader, registry
from deathtg.profile_store import save_profile_settings, update_env_value
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import scan_module_source

router = APIRouter()
USER_STATIC_DIR = Path(__file__).resolve().parent / "static" / "user"

@router.post("/profile/save")
async def save_profile(request: Request, profile_title: str = Form(""), description: str = Form(""), 
                       accent: str = Form("blue"), command_prefix: str = Form(".")):
    if not request.session.get("auth"): return RedirectResponse("/login", status_code=303)
    save_profile_settings(profile_title=profile_title, description=description, accent=accent)
    if command_prefix: update_env_value("COMMAND_PREFIX", command_prefix)
    return RedirectResponse("/profile?message=Saved", status_code=303)

@router.post("/profile/avatar")
async def upload_avatar(request: Request, avatar_base64: str = Form(...)):
    if not request.session.get("auth"): return JSONResponse({"error": "Auth"}, 401)
    try:
        data = base64.b64decode(avatar_base64.split(",")[1])
        USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
        (USER_STATIC_DIR / "avatar.png").write_bytes(data)
        return JSONResponse({"status": "ok", "avatar": f"/static/user/avatar.png?t={int(time.time())}"})
    except Exception as e: return JSONResponse({"error": str(e)}, 400)

@router.post("/modules/download")
async def download_mod(request: Request, link: str = Form(...)):
    try:
        path = await loader.download_module(link)
        name = await loader.load_file(path)
        return RedirectResponse(f"/browser?message=Installed: {name}", status_code=303)
    except Exception as e: return RedirectResponse(f"/browser?error={str(e)}", status_code=303)

@router.post("/modules/upload")
async def upload_mod(request: Request, file: UploadFile = File(...)):
    try:
        target = MODULES_DIR / file.filename
        content = await file.read()
        target.write_bytes(content)
        name = await loader.load_file(target)
        return RedirectResponse(f"/installed?message=Uploaded: {name}", status_code=303)
    except Exception as e: return RedirectResponse(f"/installed?error={str(e)}", status_code=303)

@router.post("/modules/{name}/delete")
async def delete_mod(request: Request, name: str):
    try:
        if name in PROTECTED_MODULES: raise RuntimeError("Protected")
        loader.unload(name, silent=True)
        (MODULES_DIR / f"{name}.py").unlink(missing_ok=True)
        return RedirectResponse("/installed?message=Deleted", status_code=303)
    except Exception as e: return RedirectResponse(f"/installed?error={str(e)}", status_code=303)
