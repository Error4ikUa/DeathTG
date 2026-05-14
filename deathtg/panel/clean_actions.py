from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import RedirectResponse

from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.panel.clean_core import USER_STATIC_DIR, loader, module_repo
from deathtg.registry import PROTECTED_MODULES
from deathtg.security import scan_module_source

router = APIRouter()


def redirect_with(path: str, key: str, value: str):
    base, sep, fragment = path.partition("#")
    mark = "&" if "?" in base else "?"
    url = f"{base}{mark}{key}={quote(str(value))}"
    if sep:
        url += f"#{fragment}"
    return RedirectResponse(url, status_code=303)


def ok(path: str, msg: str):
    return redirect_with(path, "message", msg)


def bad(path: str, exc: Exception):
    return redirect_with(path, "error", f"{type(exc).__name__}: {exc}")


def normalize_link(link: str) -> str:
    url = (link or "").strip().strip("'\"")
    if not url:
        raise RuntimeError("Вставь ссылку")
    if url.startswith("www."):
        url = "https://" + url
    if url.startswith("github.com/"):
        url = "https://" + url
    if "github.com" in url and "/blob/" in url:
        url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    return url


async def install_downloaded_module(link: str) -> str:
    path = await loader.download_module(link)
    try:
        return await loader.load_file(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise


async def install_uploaded_module(filename: str, text: str) -> str:
    report = scan_module_source(text)
    if not report.allowed:
        raise RuntimeError("Модуль заблокирован: " + report.pretty())
    target = MODULES_DIR / Path(filename).name
    target.write_text(text, encoding="utf-8")
    try:
        return await loader.load_file(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise


@router.post("/profile/avatar")
async def avatar_upload(file: UploadFile = File(...)):
    try:
        if not file.filename:
            raise RuntimeError("Файл не выбран")
        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise RuntimeError("Нужна картинка png/jpg/webp")
        USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
        for old in USER_STATIC_DIR.glob("avatar.*"):
            old.unlink(missing_ok=True)
        target = USER_STATIC_DIR / f"avatar{suffix}"
        target.write_bytes(await file.read())
        return ok("/profile", "Avatar updated")
    except Exception as exc:
        return bad("/profile", exc)


@router.post("/modules/download")
async def download_module(link: str = Form(...)):
    try:
        name = await install_downloaded_module(link)
        return ok("/browser#installedPane", f"Module {name} installed")
    except Exception as exc:
        return bad("/browser#installPane", exc)


@router.post("/modules/upload")
async def upload_module(file: UploadFile = File(...)):
    try:
        if not file.filename or not file.filename.endswith(".py"):
            raise RuntimeError("Нужен .py файл")
        text = (await file.read()).decode("utf-8")
        name = await install_uploaded_module(file.filename, text)
        return ok("/browser#installedPane", f"Module {name} uploaded")
    except Exception as exc:
        return bad("/browser#installPane", exc)


@router.post("/modules/update-all")
async def update_all_modules():
    try:
        updated = 0
        items = await module_repo()
        for item in items:
            link = item.get("link") or item.get("raw") or item.get("url")
            if not link:
                continue
            await install_downloaded_module(str(link))
            updated += 1
        return ok("/browser#installedPane", f"Updated modules: {updated}")
    except Exception as exc:
        return bad("/browser#installedPane", exc)


@router.post("/modules/{name}/update")
async def update_one_module(name: str):
    try:
        wanted = name.lower().replace(".py", "")
        for item in await module_repo():
            repo_name = str(item.get("name", "")).lower().replace(" ", "_")
            link = item.get("link") or item.get("raw") or item.get("url")
            if link and (repo_name == wanted or str(link).lower().endswith(f"/{wanted}.py")):
                await install_downloaded_module(str(link))
                return ok("/browser#installedPane", f"Module {name} updated")
        raise RuntimeError("Модуль не найден в DTG_Modules index.json")
    except Exception as exc:
        return bad("/browser#installedPane", exc)


@router.post("/modules/{name}/unload")
async def unload_module(name: str):
    try:
        loader.unload(name)
        return ok("/browser#installedPane", f"Module {name} unloaded")
    except Exception as exc:
        return bad("/browser#installedPane", exc)


@router.post("/modules/{name}/delete")
async def delete_module(name: str):
    try:
        if name in PROTECTED_MODULES:
            raise RuntimeError("Protected module")
        loader.unload(name, silent=True)
        path = MODULES_DIR / f"{Path(name).name}.py"
        if path.exists():
            path.unlink()
        return ok("/browser#installedPane", f"Module {name} deleted")
    except Exception as exc:
        return bad("/browser#installedPane", exc)


@router.post("/scanner/check")
async def scanner_check(source: str = Form(""), link: str = Form(""), file: UploadFile | None = File(None)):
    try:
        text = source
        if file and file.filename:
            text = (await file.read()).decode("utf-8")
        if link and not text:
            url = normalize_link(link)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=20) as response:
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
