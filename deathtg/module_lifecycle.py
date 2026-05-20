from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from deathtg.config import MODULES_DIR, RUNTIME_DIR
from deathtg.module_manager import inspect_module_path, sync_installed_modules
from deathtg.requirements_manager import install_missing_requirements
from deathtg.state_db import event, set_health, upsert

MODULE_META_PATH = RUNTIME_DIR / "module_meta.json"
VALID_ACTIONS = {
    "install",
    "update",
    "reinstall",
    "enable",
    "disable",
    "delete",
    "reload",
    "scan",
    "install_requirements",
}


@dataclass(slots=True)
class LifecycleResult:
    ok: bool
    action: str
    module_key: str
    status: str
    message: str
    details: dict[str, Any] | None = None


def _read_meta() -> dict[str, Any]:
    if not MODULE_META_PATH.exists():
        return {}
    try:
        data = json.loads(MODULE_META_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_meta(data: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    MODULE_META_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _module_path(module_key: str) -> Path | None:
    safe = Path(module_key).name.strip()
    if not safe:
        return None
    folder = MODULES_DIR / safe
    if folder.exists():
        return folder
    file_path = MODULES_DIR / f"{safe}.py"
    if file_path.exists():
        return file_path
    return None


def _record_result(result: LifecycleResult) -> LifecycleResult:
    upsert(
        "modules",
        "module_key",
        result.module_key,
        {
            "status": result.status,
            "enabled": 1 if result.status in {"installed", "enabled", "loaded", "reloaded"} else 0,
            "error": "" if result.ok else result.message,
        },
        preserve_existing=True,
        event_type="module.lifecycle",
    )
    event(
        "module.lifecycle",
        result.message,
        level="info" if result.ok else "warning",
        entity_type="module",
        entity_id=result.module_key,
        details=asdict(result),
    )
    return result


def set_module_enabled(module_key: str, enabled: bool) -> LifecycleResult:
    path = _module_path(module_key)
    if not path:
        return _record_result(LifecycleResult(False, "enable" if enabled else "disable", module_key, "missing", "Module not found"))
    meta = _read_meta()
    item = meta.get(module_key, {}) if isinstance(meta.get(module_key), dict) else {}
    item["disabled"] = not enabled
    meta[module_key] = item
    _write_meta(meta)
    status = "enabled" if enabled else "disabled"
    return _record_result(LifecycleResult(True, status, module_key, status, f"Module {module_key} {status}"))


def delete_module(module_key: str) -> LifecycleResult:
    path = _module_path(module_key)
    if not path:
        return _record_result(LifecycleResult(False, "delete", module_key, "missing", "Module not found"))
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        meta = _read_meta()
        meta.pop(module_key, None)
        _write_meta(meta)
        upsert(
            "modules",
            "module_key",
            module_key,
            {"status": "deleted", "enabled": 0, "error": ""},
            preserve_existing=False,
            event_type="module.delete",
        )
        return _record_result(LifecycleResult(True, "delete", module_key, "deleted", f"Module {module_key} deleted"))
    except Exception as exc:
        return _record_result(LifecycleResult(False, "delete", module_key, "error", f"Delete failed: {type(exc).__name__}: {exc}"))


def scan_module(module_key: str) -> LifecycleResult:
    path = _module_path(module_key)
    if not path:
        return _record_result(LifecycleResult(False, "scan", module_key, "missing", "Module not found"))
    state = inspect_module_path(path)
    if not state:
        return _record_result(LifecycleResult(False, "scan", module_key, "error", "Module entry file not found"))
    sync_installed_modules()
    return _record_result(
        LifecycleResult(
            state.antivirus_status != "blocked",
            "scan",
            module_key,
            state.status,
            f"Scan finished: {state.antivirus_status}",
            asdict(state),
        )
    )


def install_module_requirements(module_key: str) -> LifecycleResult:
    result = install_missing_requirements(module_key)
    ok = bool(result.get("ok"))
    return _record_result(
        LifecycleResult(
            ok,
            "install_requirements",
            module_key,
            "requirements_ok" if ok else "missing_requirements",
            str(result.get("message") or "Requirements install finished")[-800:],
            result,
        )
    )


def mark_module_reload(module_key: str) -> LifecycleResult:
    path = _module_path(module_key)
    if not path:
        return _record_result(LifecycleResult(False, "reload", module_key, "missing", "Module not found"))
    return _record_result(LifecycleResult(True, "reload", module_key, "reload_requested", f"Reload requested for {module_key}"))


def lifecycle_action(module_key: str, action: str) -> LifecycleResult:
    action = action.strip().lower()
    module_key = Path(module_key).name.strip()
    if action not in VALID_ACTIONS:
        return _record_result(LifecycleResult(False, action, module_key, "error", f"Unknown lifecycle action: {action}"))
    if action == "enable":
        return set_module_enabled(module_key, True)
    if action == "disable":
        return set_module_enabled(module_key, False)
    if action == "delete":
        return delete_module(module_key)
    if action == "scan":
        return scan_module(module_key)
    if action == "install_requirements":
        return install_module_requirements(module_key)
    if action in {"reload", "update", "reinstall", "install"}:
        return mark_module_reload(module_key)
    return _record_result(LifecycleResult(False, action, module_key, "error", "Unhandled lifecycle action"))


def sync_lifecycle_health() -> dict[str, int]:
    modules = sync_installed_modules()
    total = len(modules)
    enabled = sum(1 for item in modules if item.enabled)
    disabled = sum(1 for item in modules if item.status == "disabled")
    blocked = sum(1 for item in modules if item.status == "blocked")
    error = sum(1 for item in modules if item.status == "error")
    result = {"total": total, "enabled": enabled, "disabled": disabled, "blocked": blocked, "error": error}
    set_health("module_lifecycle", "ok" if not blocked and not error else "warning", "Module lifecycle state synced", result)
    return result
