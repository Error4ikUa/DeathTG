from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import aiohttp
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from deathtg.config import MODULES_DIR, ROOT_DIR, RUNTIME_DIR
from deathtg.panel.clean_core import MODULE_META_PATH, loader, refresh_modules, _extract_module_source_meta
from deathtg.profile_store import profile_settings, save_profile_settings, update_env_value
from deathtg.registry import PROTECTED_MODULES
from deathtg.module_repo import fetch_module_bundle, parse_requirements_text
from deathtg.module_config import ModuleConfig, ValidationError
from deathtg.role_gate import can_assign_role, normalize_role
from deathtg.community_roles import clear_role_scan_result, read_role_scan_result
from deathtg.security import is_trusted_module_link, scan_module_source

router = APIRouter()

USER_STATIC_DIR = Path(__file__).resolve().parent / "static" / "user"
PANEL_ACTIONS_PATH = RUNTIME_DIR / "panel_actions.jsonl"
PENDING_INSTALLS_DIR = RUNTIME_DIR / "pending_installs"
REQUIREMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:==[A-Za-z0-9_.!+-]+|>=[A-Za-z0-9_.!+-]+|<=[A-Za-z0-9_.!+-]+)?$")
ROLE_SCAN_TIMEOUT_SECONDS = 15.0


def _load_module_meta() -> dict[str, dict]:
    if not MODULE_META_PATH.exists():
        return {}
    try:
        return json.loads(MODULE_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_module_meta(data: dict[str, dict]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    MODULE_META_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _set_module_meta(module_name: str, **payload: object) -> None:
    meta = _load_module_meta()
    meta[module_name] = {"updated_at": int(time.time()), **payload}
    _save_module_meta(meta)


def _drop_module_meta(module_name: str) -> None:
    meta = _load_module_meta()
    if module_name in meta:
        meta.pop(module_name, None)
        _save_module_meta(meta)


def _redirect(path: str, *, message: str | None = None, error: str | None = None) -> RedirectResponse:
    key = "message" if message is not None else "error"
    value = message if message is not None else error
    if value:
        return RedirectResponse(f"{path}?{key}={quote(str(value))}", status_code=303)
    return RedirectResponse(path, status_code=303)


def _target_path(return_to: str | None, default: str = "/browser") -> str:
    value = (return_to or "").strip().lower()
    if value == "news":
        return "/"
    if value == "installed_tab":
        return "/browser#installedPane"
    if value == "install_tab":
        return "/browser#installPane"
    if value == "browser":
        return "/browser"
    if value == "installed":
        return "/installed"
    return default


def _require_auth(request: Request) -> RedirectResponse | None:
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return None


def _safe_module_name(name: str) -> str:
    stem = Path(name).stem
    if not stem or stem != name or any(part in stem for part in ("/", "\\", "..")):
        raise RuntimeError("Invalid module name")
    return stem


def _queue_userbot_action(action: str, **payload: object) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    item = {"action": action, "ts": int(time.time())}
    item.update(payload)
    with PANEL_ACTIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


async def _request_role_scan(role: str) -> tuple[bool, str]:
    request_id = secrets.token_urlsafe(12)
    clear_role_scan_result(request_id)
    _queue_userbot_action("role_scan", request_id=request_id, role=normalize_role(role))
    deadline = time.monotonic() + ROLE_SCAN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        payload = read_role_scan_result(request_id)
        if payload is not None:
            clear_role_scan_result(request_id)
            if bool(payload.get("ok")):
                return True, ""
            message = str(payload.get("message") or "").strip()
            return False, message or "Role verification was denied."
        await asyncio.sleep(0.35)
    clear_role_scan_result(request_id)
    return False, "Role verification timed out. Open Telegram and confirm your DeathTG access first."


def _report_payload(report) -> dict:
    return {
        "allowed": report.allowed,
        "score": report.score,
        "severity": report.severity,
        "verdict": report.verdict,
        "trusted": report.trusted,
        "reasons": list(report.reasons),
        "findings": [
            {
                "line": item.line,
                "reason": item.reason,
                "score": item.score,
                "code": item.code,
            }
            for item in getattr(report, "findings", [])
        ],
        "pretty": report.pretty(),
    }


def _pending_path(token: str) -> Path:
    safe = "".join(ch for ch in token if ch.isalnum() or ch in {"_", "-"})
    return PENDING_INSTALLS_DIR / f"{safe}.json"


def _save_pending_install(
    *,
    filename: str,
    source: str,
    link: str,
    source_type: str,
    trusted: bool,
    report,
    image: str = "",
    description: str = "",
    author: str = "",
    version: str = "",
    install_kind: str = "file",
    module_name: str = "",
    image_name: str = "",
    requirements_text: str = "",
) -> str:
    PENDING_INSTALLS_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18)
    _pending_path(token).write_text(
        json.dumps(
            {
                "filename": Path(filename).name,
                "source": source,
                "link": link,
                "source_type": source_type,
                "trusted": trusted,
                "image": image,
                "description": description,
                "author": author,
                "version": version,
                "install_kind": install_kind,
                "module_name": module_name,
                "image_name": image_name,
                "requirements_text": requirements_text,
                "report": _report_payload(report),
                "created_at": int(time.time()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return token


def load_pending_install(token: str | None) -> dict | None:
    if not token:
        return None
    path = _pending_path(token)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data["token"] = token
    return data


async def _download_module_source(link: str) -> tuple[str, str]:
    bundle = await fetch_module_bundle(link)
    filename = str(bundle.get("entry_filename") or "module.py")
    text = str(bundle.get("source") or "")
    if not filename.endswith(".py"):
        raise RuntimeError("Link must point to a .py module or a GitHub module folder")
    if loader._looks_like_html(text):
        raise RuntimeError("URL returned HTML, not Python code. Use a raw/blob .py link or a GitHub module folder")
    return bundle


async def _install_module_source(
    *,
    filename: str,
    source: str,
    link: str,
    source_type: str,
    trusted: bool,
    force: bool = False,
    image: str = "",
    description: str = "",
    author: str = "",
    version: str = "",
    install_kind: str = "file",
    module_name: str = "",
    image_name: str = "",
    requirements_text: str = "",
) -> str:
    safe_name = Path(filename).name
    if not safe_name.endswith(".py"):
        raise RuntimeError("Module filename must end with .py")
    final_module_name = _safe_module_name(module_name or Path(safe_name).stem)
    report = scan_module_source(source, trusted=trusted)
    if report.severity in {"warning", "danger"} and not trusted and not force:
        token = _save_pending_install(
            filename=safe_name,
            source=source,
            link=link,
            source_type=source_type,
            trusted=trusted,
            report=report,
            image=image,
            description=description,
            author=author,
            version=version,
            install_kind=install_kind,
            module_name=final_module_name,
            image_name=image_name,
            requirements_text=requirements_text,
        )
        raise RuntimeError(f"SECURITY_PENDING:{token}")
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    if install_kind == "folder":
        target = MODULES_DIR / final_module_name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.mkdir(parents=True, exist_ok=True)
        (target / safe_name).write_text(source, encoding="utf-8")
        if requirements_text.strip():
            (target / "requirements.txt").write_text(requirements_text, encoding="utf-8")
        if image:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image, timeout=20) as response:
                        if response.status == 200:
                            (target / "Module.png").write_bytes(await response.read())
            except Exception:
                pass
    else:
        target = MODULES_DIR / safe_name
        target.write_text(source, encoding="utf-8")
    try:
        name = await loader.load_file(target, force=trusted or force, module_name=final_module_name if install_kind == "folder" else None)
    except Exception:
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        raise
    verified = bool(trusted or (not report.findings and report.allowed and not force))
    _set_module_meta(
        name,
        verified=verified,
        security_override=bool(force and not trusted),
        security_verdict=report.verdict,
        security_score=report.score,
        security_findings=_report_payload(report)["findings"],
        source_link=link,
        source_type=source_type,
        image=image,
        description=description,
        author=author,
        version=version,
        filename=(f"{final_module_name}/{safe_name}" if install_kind == "folder" else safe_name),
    )
    _queue_userbot_action("install", path=str(target), force=trusted or force)
    await refresh_modules()
    return name


@router.post("/profile/save")
async def save_profile(
    request: Request,
    profile_title: str = Form(""),
    description: str = Form(""),
    info_text: str = Form(""),
    accent: str = Form("blue"),
    role: str = Form("user"),
    language: str = Form("en"),
    command_prefix: str = Form("."),
):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    current = profile_settings()
    role = normalize_role(role)
    allowed, error = can_assign_role(
        current_role=str(current.get("role") or "user"),
        requested_role=role,
    )
    if not allowed and role in {"admin", "developer"}:
        allowed, scan_error = await _request_role_scan(role)
        if not allowed and scan_error:
            error = scan_error
    if not allowed:
        return _redirect("/profile", error=error)
    save_profile_settings(
        profile_title=profile_title,
        description=description,
        info_text=info_text,
        accent=accent,
        role=role,
        language=language,
    )
    if command_prefix:
        update_env_value("COMMAND_PREFIX", command_prefix)
    return _redirect("/profile", message="Saved.")


@router.post("/profile/avatar")
async def upload_avatar(
    request: Request,
    avatar_base64: str | None = Form(None),
    avatar_file: UploadFile | None = File(None),
):
    if _require_auth(request):
        return JSONResponse({"error": "Auth"}, 401)
    try:
        USER_STATIC_DIR.mkdir(parents=True, exist_ok=True)
        data: bytes
        if avatar_file and avatar_file.filename:
            data = await avatar_file.read()
            if not data:
                raise RuntimeError("Avatar file is empty")
            (USER_STATIC_DIR / "avatar.png").write_bytes(data)
            _queue_userbot_action("startup_sync")
            return RedirectResponse("/profile?message=Avatar saved", status_code=303)
        if not avatar_base64:
            raise RuntimeError("Avatar payload is empty")
        data = base64.b64decode(avatar_base64.split(",", 1)[1])
        (USER_STATIC_DIR / "avatar.png").write_bytes(data)
        _queue_userbot_action("startup_sync")
        return JSONResponse({"status": "ok", "avatar": f"/static/user/avatar.png?t={int(time.time())}"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


@router.post("/modules/download")
async def download_mod(
    request: Request,
    link: str = Form(...),
    return_to: str = Form("browser"),
    image: str = Form(""),
    description: str = Form(""),
    author: str = Form(""),
    version: str = Form(""),
):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        bundle = await _download_module_source(link)
        trusted = bool(bundle.get("trusted")) or is_trusted_module_link(link)
        name = await _install_module_source(
            filename=str(bundle.get("entry_filename") or "module.py"),
            source=str(bundle.get("source") or ""),
            link=link,
            source_type="repo" if trusted else "url",
            trusted=trusted,
            image=str(bundle.get("image_url") or image or ""),
            description=str(description or bundle.get("description") or ""),
            author=str(author or bundle.get("author") or ""),
            version=str(version or bundle.get("version") or ""),
            install_kind=str(bundle.get("kind") or "file"),
            module_name=str(bundle.get("module_name") or ""),
            image_name=str(bundle.get("image_name") or ""),
            requirements_text=str(bundle.get("requirements_text") or ""),
        )
        return _redirect(_target_path(return_to), message=f"Installed: {name}")
    except Exception as e:
        text = str(e)
        if text.startswith("SECURITY_PENDING:"):
            token = text.split(":", 1)[1]
            return RedirectResponse(f"{_target_path(return_to)}?warning={quote(token)}", status_code=303)
        return _redirect(_target_path(return_to), error=str(e))


@router.post("/modules/upload")
async def upload_mod(request: Request, file: UploadFile = File(...), return_to: str = Form("browser")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        filename = Path(file.filename or "").name
        if not filename.endswith(".py"):
            raise RuntimeError("Upload a .py module file")
        content = await file.read()
        if not content:
            raise RuntimeError("Uploaded file is empty")
        text = content.decode("utf-8", errors="replace")
        name = await _install_module_source(
            filename=filename,
            source=text,
            link="",
            source_type="upload",
            trusted=False,
            description="Uploaded local module",
            install_kind="file",
        )
        return _redirect(_target_path(return_to), message=f"Uploaded: {name}")
    except Exception as e:
        text = str(e)
        if text.startswith("SECURITY_PENDING:"):
            token = text.split(":", 1)[1]
            return RedirectResponse(f"{_target_path(return_to)}?warning={quote(token)}", status_code=303)
        return _redirect(_target_path(return_to), error=str(e))


@router.post("/modules/pending/{token}/delete")
async def delete_pending_mod(request: Request, token: str, return_to: str = Form("browser")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    _pending_path(token).unlink(missing_ok=True)
    return _redirect(_target_path(return_to), message="Module install cancelled")


@router.post("/modules/pending/{token}/continue")
async def continue_pending_mod(request: Request, token: str, return_to: str = Form("browser")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    pending = load_pending_install(token)
    if not pending:
        return _redirect(_target_path(return_to), error="Pending module was not found")
    try:
        name = await _install_module_source(
            filename=str(pending.get("filename") or "module.py"),
            source=str(pending.get("source") or ""),
            link=str(pending.get("link") or ""),
            source_type=str(pending.get("source_type") or "url"),
            trusted=bool(pending.get("trusted")),
            force=True,
            image=str(pending.get("image") or ""),
            description=str(pending.get("description") or ""),
            author=str(pending.get("author") or ""),
            version=str(pending.get("version") or ""),
            install_kind=str(pending.get("install_kind") or "file"),
            module_name=str(pending.get("module_name") or ""),
            image_name=str(pending.get("image_name") or ""),
            requirements_text=str(pending.get("requirements_text") or ""),
        )
        _pending_path(token).unlink(missing_ok=True)
        return _redirect(_target_path(return_to), message=f"Installed with warning: {name}")
    except Exception as e:
        return _redirect(_target_path(return_to), error=str(e))


@router.post("/modules/{name}/unload")
async def unload_mod(request: Request, name: str, return_to: str = Form("browser")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        module_name = _safe_module_name(name)
        if module_name in PROTECTED_MODULES:
            raise RuntimeError("Protected module cannot be unloaded")
        loader.unload(module_name)
        _queue_userbot_action("unload", module=module_name)
        return _redirect(_target_path(return_to), message=f"Unloaded: {module_name}")
    except Exception as e:
        return _redirect(_target_path(return_to), error=str(e))


@router.post("/modules/{name}/delete")
async def delete_mod(request: Request, name: str, return_to: str = Form("browser")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        module_name = _safe_module_name(name)
        if module_name in PROTECTED_MODULES:
            raise RuntimeError("Protected module cannot be deleted")
        target = loader.module_path(module_name)
        if not target or not target.exists():
            raise RuntimeError("Module file not found")
        loader.unload(module_name, silent=True)
        target = target.resolve()
        modules_root = MODULES_DIR.resolve()
        if modules_root not in target.parents and target != modules_root:
            raise RuntimeError("Unsafe module path")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        _drop_module_meta(module_name)
        _queue_userbot_action("delete", module=module_name)
        await refresh_modules()
        return _redirect(_target_path(return_to), message=f"Deleted: {module_name}")
    except Exception as e:
        return _redirect(_target_path(return_to), error=str(e))


@router.post("/modules/{name}/update")
async def update_mod(request: Request, name: str, return_to: str = Form("browser")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        module_name = _safe_module_name(name)
        if module_name in PROTECTED_MODULES:
            raise RuntimeError("Protected module cannot be updated from the panel")
        path = loader.module_path(module_name)
        if not path or not path.exists():
            raise RuntimeError("Update is not implemented for this module yet")
        meta = _load_module_meta().get(module_name, {})
        force = bool(meta.get("verified") or meta.get("security_override"))
        await loader.load_file(path, force=force)
        _queue_userbot_action("install", path=str(path), force=force)
        return _redirect(_target_path(return_to), message=f"Reloaded: {module_name}")
    except Exception as e:
        return _redirect(_target_path(return_to), error=str(e))


@router.post("/modules/{name}/config")
async def save_module_config(request: Request, name: str, return_to: str = Form("module")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        module_name = _safe_module_name(name)
        form = await request.form()
        instances = loader.instances.get(module_name, [])
        if not instances:
            raise RuntimeError("Module is not loaded")
        changed = 0
        for inst in instances:
            cfg = getattr(inst, "config", None)
            if not isinstance(cfg, ModuleConfig):
                continue
            for item in cfg.values():
                key = getattr(item, "name", "")
                if not key or key not in form:
                    continue
                values = [str(value) for value in form.getlist(key)]
                lower_values = [value.lower() for value in values]
                raw_value = "true" if "true" in lower_values else (values[-1] if values else "")
                if getattr(item, "secret", False) and raw_value in {"", "***"}:
                    continue
                cfg[key] = raw_value
                changed += 1
            inst.save_config()
        if not changed:
            return _redirect(f"/modules/{module_name}", message="No config changes")
        _queue_userbot_action("reload_config", module=module_name)
        return _redirect(f"/modules/{module_name}", message=f"Config saved: {changed} value(s)")
    except ValidationError as exc:
        return _redirect(f"/modules/{name}", error=f"Config validation failed: {exc}")
    except Exception as e:
        return _redirect(f"/modules/{name}", error=str(e))


@router.post("/modules/{name}/requirements")
async def install_module_requirements(request: Request, name: str):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        module_name = _safe_module_name(name)
        path = loader.module_source_path(module_name)
        if not path or not path.exists():
            path = ROOT_DIR / "deathtg" / "modules" / f"{module_name}.py"
        if not path.exists():
            raise RuntimeError("Module source was not found")
        parsed = _extract_module_source_meta(path.read_text(encoding="utf-8", errors="replace"))
        requirements = [item for item in parsed.get("requires", []) if REQUIREMENT_RE.fullmatch(item)]
        requirements_file = path.parent / "requirements.txt"
        if requirements_file.exists():
            requirements.extend(
                item
                for item in parse_requirements_text(requirements_file.read_text(encoding="utf-8", errors="replace"))
                if REQUIREMENT_RE.fullmatch(item)
            )
        requirements = sorted(set(requirements))
        if not requirements:
            raise RuntimeError("No safe requirements found")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *requirements],
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            timeout=180,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode != 0:
            raise RuntimeError((output or "pip failed")[-800:])
        return _redirect(f"/modules/{module_name}", message=f"Requirements installed: {', '.join(requirements)}")
    except Exception as e:
        return _redirect(f"/modules/{name}", error=str(e))


@router.post("/modules/update-all")
async def update_all_modules(request: Request, return_to: str = Form("browser")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    try:
        await refresh_modules()
        _queue_userbot_action("reload_all")
        return _redirect(_target_path(return_to), message="Update all checked")
    except Exception as e:
        return _redirect(_target_path(return_to), error=str(e))


@router.post("/runtime/sync")
async def runtime_sync(request: Request, return_to: str = Form("profile")):
    blocked = _require_auth(request)
    if blocked:
        return blocked
    _queue_userbot_action("startup_sync")
    return _redirect(_target_path(return_to, "/profile"), message="Runtime sync queued")
