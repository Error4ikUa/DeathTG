from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from deathtg.health_tools import collect_requirement_state, load_health_state, safe_mode_enabled
from deathtg.panel.clean_core import has_env, profile_info, startup_status, status, templates
from deathtg.startup_state import startup_snapshot
from deathtg.state_db import connect, ensure_state_db, recent_events

router = APIRouter()


def _auth_guard(request: Request):
    if not has_env():
        return RedirectResponse("/setup", status_code=303)
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
    return None


def _decode(value: Any) -> Any:
    if value in (None, ""):
        return value
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _rows(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    ensure_state_db()
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _health_rows() -> list[dict[str, Any]]:
    rows = _rows("SELECT * FROM health_checks ORDER BY checked_at DESC")
    for row in rows:
        row["details"] = _decode(row.pop("details_json", ""))
    return rows


def _bot_rows() -> list[dict[str, Any]]:
    return _rows("SELECT * FROM bots ORDER BY bot_key")


def _resource_rows() -> list[dict[str, Any]]:
    rows = _rows("SELECT * FROM telegram_resources ORDER BY resource_type, resource_key")
    for row in rows:
        row["metadata"] = _decode(row.pop("metadata_json", ""))
    return rows


def _module_rows() -> list[dict[str, Any]]:
    return _rows("SELECT * FROM modules ORDER BY module_key")


def _antivirus_rows() -> list[dict[str, Any]]:
    rows = _rows("SELECT * FROM antivirus_reports ORDER BY scanned_at DESC")
    for row in rows:
        row["findings"] = _decode(row.pop("findings_json", ""))
    return rows


def _requirement_rows() -> list[dict[str, Any]]:
    return _rows("SELECT module_key, requirement, installed, error, updated_at FROM module_requirements ORDER BY installed, module_key, requirement")


def _settings_rows() -> list[dict[str, Any]]:
    return _rows("SELECT key, value, updated_at FROM settings ORDER BY key")


def _summary() -> dict[str, Any]:
    health = _health_rows()
    bots = _bot_rows()
    resources = _resource_rows()
    modules = _module_rows()
    antivirus = _antivirus_rows()
    requirements = _requirement_rows()
    return {
        "health_total": len(health),
        "health_bad": sum(1 for item in health if str(item.get("status") or "").lower() not in {"ok", "disabled"}),
        "bots_total": len(bots),
        "bots_bad": sum(1 for item in bots if str(item.get("status") or "").lower() not in {"configured", "not_configured", "ok"}),
        "resources_total": len(resources),
        "resources_bad": sum(1 for item in resources if str(item.get("status") or "").lower() not in {"ok", "ready"}),
        "modules_total": len(modules),
        "modules_bad": sum(1 for item in modules if str(item.get("status") or "").lower() in {"error", "blocked"}),
        "antivirus_total": len(antivirus),
        "antivirus_blocked": sum(1 for item in antivirus if not int(item.get("allowed") or 0)),
        "requirements_total": len(requirements),
        "requirements_missing": sum(1 for item in requirements if not int(item.get("installed") or 0)),
    }


def state_payload() -> dict[str, Any]:
    return {
        "summary": _summary(),
        "health": _health_rows(),
        "bots": _bot_rows(),
        "resources": _resource_rows(),
        "modules": _module_rows(),
        "antivirus": _antivirus_rows(),
        "requirements": _requirement_rows(),
        "settings": _settings_rows(),
        "events": recent_events(80),
    }


@router.get("/health")
async def health_page(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return blocked
    payload = state_payload()
    profile = await profile_info()
    health_state = load_health_state()
    return templates.TemplateResponse(
        "clean_health.html",
        {
            "request": request,
            "lang": "en",
            "page": "health",
            "profile": profile,
            "status": await status(profile),
            "startup": startup_status(),
            "startup_snapshot": startup_snapshot(),
            "health_state": health_state,
            "requirements_state": health_state.get("requirements_state") or collect_requirement_state(),
            "safe_mode_enabled": safe_mode_enabled(),
            **payload,
        },
    )


@router.get("/state")
async def state_page(request: Request):
    return await health_page(request)


@router.get("/api/state")
async def api_state(request: Request):
    blocked = _auth_guard(request)
    if blocked:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    return JSONResponse({"ok": True, **state_payload()})


@router.get("/api/state/{table}")
async def api_state_table(request: Request, table: str):
    blocked = _auth_guard(request)
    if blocked:
        return JSONResponse({"ok": False, "error": "auth required"}, status_code=401)
    payload = state_payload()
    if table not in payload:
        return JSONResponse({"ok": False, "error": "unknown table"}, status_code=404)
    return JSONResponse({"ok": True, table: payload[table]})
