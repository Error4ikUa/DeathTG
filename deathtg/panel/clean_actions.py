from __future__ import annotations

import base64
import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.panel.clean_core import loader, registry, templates
from deathtg.profile_store import save_profile_settings, update_env_value
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import scan_module_source

router = APIRouter()
USER_STATIC_DIR = Path(__file__).resolve().parent / "static" / "user"

# --- ПРОФИЛЬ И АВАТАР ---

@router.post("/profile/save")
async def save_profile_endpoint(
    request: Request,
    profile_title: str = Form("DeathTG Operator"),
    description: str = Form(""),
    language: str = Form("en"),
    accent: str = Form("blue"),
    command_prefix: str = Form("."),
    anon_mode: str | None = Form(None),
    auto_metrics: str | None = Form(None),
    strict_security: str | None = Form(None)
):
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
        
    safe_anon = "1" if anon_mode else "0"
    safe_metrics = "1" if auto_metrics else "0"
    safe_security = "1" if strict_security else "0"

    save_profile_settings(
        profile_title=profile_title,
        description=description,
        language=language,
        accent=accent,
        anon_mode=safe_anon,
        auto_metrics=safe_metrics,
        strict_security=safe_security
    )
    
    if command_prefix and len(command_prefix) <= 3:
        update_env_value("COMMAND_PREFIX", command_prefix.strip())
        
    return RedirectResponse("/profile?message=Настройки профиля и системы успешно применены!", status_code=303)


@router.post("/profile/avatar")
async def upload_avatar(request: Request, avatar_base64: str = Form(...)):
    if not request.session.get("auth"):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        if "," in avatar_base64:
            _, encoded = avatar_base64.split(",", 1)
        else:
            encoded = avatar_base64
            
        data = base64.b64decode(encoded)
        USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
        avatar_path = USER_STATIC_DIR / "avatar.png"
        avatar_path.write_bytes(data)
        
        return JSONResponse({"status": "ok", "avatar": f"/static/user/avatar.png?t={int(time.time())}"})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


# --- ЛОГИКА УПРАВЛЕНИЯ МОДУЛЯМИ ---

@router.post("/modules/download")
async def download_module(request: Request, link: str = Form(...)):
    if not request.session.get("auth"): return RedirectResponse("/login", status_code=303)
    try:
        path = await loader.download_module(link)
        module_name = await loader.load_file(path)
        return RedirectResponse(f"/browser?message=Модуль {module_name} успешно установлен", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/browser?error={type(exc).__name__}: {exc}", status_code=303)

@router.post("/modules/upload")
async def upload_module(request: Request, file: UploadFile = File(...)):
    if not request.session.get("auth"): return RedirectResponse("/login", status_code=303)
    try:
        if not file.filename or not file.filename.endswith(".py"):
            raise RuntimeError("Требуется файл .py")
        text = (await file.read()).decode("utf-8")
        report = scan_module_source(text)
        if not report.allowed:
            raise RuntimeError("Модуль заблокирован AST-защитой: " + report.pretty())
        target = MODULES_DIR / Path(file.filename).name
        target.write_text(text, encoding="utf-8")
        module_name = await loader.load_file(target)
        return RedirectResponse(f"/installed?message=Модуль {module_name} успешно загружен", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/installed?error={type(exc).__name__}: {exc}", status_code=303)

@router.post("/modules/{name}/unload")
async def unload_module(request: Request, name: str):
    if not request.session.get("auth"): return RedirectResponse("/login", status_code=303)
    try:
        loader.unload(name)
        return RedirectResponse(f"/installed?message=Модуль {name} выгружен из памяти", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/installed?error={type(exc).__name__}: {exc}", status_code=303)

@router.post("/modules/{name}/delete")
async def delete_module(request: Request, name: str):
    if not request.session.get("auth"): return RedirectResponse("/login", status_code=303)
    try:
        if name in PROTECTED_MODULES:
            raise RuntimeError("Этот модуль защищен системой (Core) и не может быть удален")
        loader.unload(name, silent=True)
        path = MODULES_DIR / f"{Path(name).name}.py"
        if path.exists(): path.unlink()
        return RedirectResponse(f"/installed?message=Модуль {name} безвозвратно удален", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/installed?error={type(exc).__name__}: {exc}", status_code=303)

@router.post("/scan")
async def scan_text(request: Request, source: str = Form(...)):
    if not request.session.get("auth"): return RedirectResponse("/login", status_code=303)
    report = scan_module_source(source)
    verdict = "ALLOWED" if report.allowed else "BLOCKED"
    return templates.TemplateResponse("clean_scanner.html", {"request": request, "report": report, "verdict": verdict, "source": source})

@router.post("/update")
async def update_project(request: Request):
    if not request.session.get("auth"): return RedirectResponse("/login", status_code=303)
    try:
        result = subprocess.run(["git", "pull"], cwd=ROOT_DIR, text=True, capture_output=True, timeout=60)
        output = (result.stdout + "\n" + result.stderr).strip()
        return RedirectResponse(f"/?message={output[-180:]}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/?error={type(exc).__name__}: {exc}", status_code=303)
