from __future__ import annotations

import subprocess
from pathlib import Path

import aiohttp
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import RedirectResponse

from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.panel.clean_core import loader
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import scan_module_source

router = APIRouter()


def ok(path: str, msg: str):
    return RedirectResponse(f"{path}?message={msg}", status_code=303)


def bad(path: str, exc: Exception):
    return RedirectResponse(f"{path}?error={type(exc).__name__}: {exc}", status_code=303)


@router.post("/modules/download")
async def download_module(link: str = Form(...)):
    try:
        path = await loader.download_module(link)
        name = await loader.load_file(path)
        return ok("/installed", f"Module {name} installed")
    except Exception as exc:
        return bad("/install", exc)


@router.post("/modules/upload")
async def upload_module(file: UploadFile = File(...)):
    try:
        if not file.filename or not file.filename.endswith(".py"):
            raise RuntimeError("Нужен .py файл")
        text = (await file.read()).decode("utf-8")
        report = scan_module_source(text)
        if not report.allowed:
            raise RuntimeError("Модуль заблокирован: " + report.pretty())
        target = MODULES_DIR / Path(file.filename).name
        target.write_text(text, encoding="utf-8")
        name = await loader.load_file(target)
        return ok("/installed", f"Module {name} uploaded")
    except Exception as exc:
        return bad("/install", exc)


@router.post("/modules/{name}/unload")
async def unload_module(name: str):
    try:
        loader.unload(name)
        return ok("/installed", f"Module {name} unloaded")
    except Exception as exc:
        return bad("/installed", exc)


@router.post("/modules/{name}/delete")
async def delete_module(name: str):
    try:
        if name in PROTECTED_MODULES:
            raise RuntimeError("Protected module")
        loader.unload(name, silent=True)
        path = MODULES_DIR / f"{Path(name).name}.py"
        if path.exists():
            path.unlink()
        return ok("/installed", f"Module {name} deleted")
    except Exception as exc:
        return bad("/installed", exc)


@router.post("/scanner/check")
async def scanner_check(source: str = Form(""), link: str = Form(""), file: UploadFile | None = File(None)):
    try:
        text = source
        if file and file.filename:
            text = (await file.read()).decode("utf-8")
        if link and not text:
            async with aiohttp.ClientSession() as session:
                async with session.get(link, timeout=20) as response:
                    text = await response.text()
        if not text.strip():
            raise RuntimeError("Вставь ссылку, файл или код")
        report = scan_module_source(text)
        verdict = "ALLOWED" if report.allowed else "BLOCKED"
        return RedirectResponse(f"/scanner?message=Scanner verdict: {verdict}, score {report.score}", status_code=303)
    except Exception as exc:
        return bad("/scanner", exc)


@router.post("/update")
async def update_project():
    try:
        result = subprocess.run(["git", "pull"], cwd=ROOT_DIR, text=True, capture_output=True, timeout=60)
        output = (result.stdout + "\n" + result.stderr).strip()[-220:] or "Already up to date"
        return ok("/", output)
    except Exception as exc:
        return bad("/", exc)
