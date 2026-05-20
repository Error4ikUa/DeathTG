from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from deathtg.assets import resolve_module_entry
from deathtg.config import MODULES_DIR, RUNTIME_DIR
from deathtg.security import scan_module_source
from deathtg.state_db import set_health, upsert

MODULE_META_PATH = RUNTIME_DIR / "module_meta.json"


@dataclass(slots=True)
class ModuleState:
    module_key: str
    name: str
    status: str
    enabled: bool
    path: str
    entry: str
    source_url: str = ""
    version: str = ""
    author: str = ""
    has_requirements: bool = False
    requirements: list[str] | None = None
    antivirus_status: str = "unknown"
    error: str = ""


def _safe_module_key(path: Path) -> str:
    return path.stem if path.is_file() else path.name


def _read_meta() -> dict[str, Any]:
    if not MODULE_META_PATH.exists():
        return {}
    try:
        data = json.loads(MODULE_META_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _requirements_from_text(text: str) -> list[str]:
    reqs: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        reqs.append(stripped)
    return reqs


def _inline_requires(source: str) -> list[str]:
    reqs: list[str] = []
    for line in source.splitlines()[:60]:
        low = line.lower().strip()
        if low.startswith("# requires:") or low.startswith("#requires:"):
            _, _, tail = line.partition(":")
            reqs.extend(part.strip() for part in tail.replace(",", " ").split() if part.strip())
    return reqs


def _extract_metadata(source: str) -> dict[str, str]:
    meta = {"name": "", "version": "", "author": ""}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        meta["error"] = f"SyntaxError: {exc}"
        return meta
    for node in tree.body[:80]:
        if isinstance(node, ast.Assign):
            keys = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value = node.value.value if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) else ""
            for key in keys:
                low = key.lower().strip("_")
                if low in {"name", "module_name"} and value:
                    meta["name"] = value
                elif low in {"version"} and value:
                    meta["version"] = value
                elif low in {"author", "authors"} and value:
                    meta["author"] = value
    return meta


def inspect_module_path(path: Path, meta_db: dict[str, Any] | None = None) -> ModuleState | None:
    meta_db = meta_db or _read_meta()
    if path.name.startswith("_"):
        return None
    if path.is_file() and path.suffix.lower() != ".py":
        return None
    entry = resolve_module_entry(path)
    if not entry or not entry.exists() or entry.suffix.lower() != ".py":
        return None

    module_key = _safe_module_key(path)
    module_meta = meta_db.get(module_key, {}) if isinstance(meta_db.get(module_key), dict) else {}
    source = ""
    error = ""
    antivirus_status = "unknown"
    try:
        source = entry.read_text(encoding="utf-8")
        report = scan_module_source(source, trusted=bool(module_meta.get("verified") or module_meta.get("security_override")))
        antivirus_status = "trusted" if report.allowed else "blocked"
        if not report.allowed:
            error = report.pretty()
    except Exception as exc:
        error = f"read/scan failed: {type(exc).__name__}: {exc}"
        antivirus_status = "error"

    parsed = _extract_metadata(source) if source else {}
    if parsed.get("error") and not error:
        error = str(parsed["error"])

    requirements: list[str] = []
    req_file = (path / "requirements.txt") if path.is_dir() else (path.with_name("requirements.txt") if path.parent != MODULES_DIR else Path())
    if req_file and req_file.exists():
        try:
            requirements.extend(_requirements_from_text(req_file.read_text(encoding="utf-8")))
        except Exception as exc:
            error = error or f"requirements read failed: {exc}"
    requirements.extend(item for item in _inline_requires(source) if item not in requirements)

    status = "installed"
    if error:
        status = "blocked" if antivirus_status == "blocked" else "error"
    if module_meta.get("disabled"):
        status = "disabled"

    return ModuleState(
        module_key=module_key,
        name=str(parsed.get("name") or module_meta.get("name") or module_key),
        status=status,
        enabled=status != "disabled",
        path=str(path),
        entry=str(entry),
        source_url=str(module_meta.get("url") or module_meta.get("source_url") or ""),
        version=str(parsed.get("version") or module_meta.get("version") or ""),
        author=str(parsed.get("author") or module_meta.get("author") or ""),
        has_requirements=bool(requirements),
        requirements=requirements,
        antivirus_status=antivirus_status,
        error=error,
    )


def sync_module_state(module: ModuleState) -> None:
    upsert(
        "modules",
        "module_key",
        module.module_key,
        {
            "name": module.name,
            "status": module.status,
            "enabled": 1 if module.enabled else 0,
            "source_url": module.source_url,
            "version": module.version,
            "author": module.author,
            "has_requirements": 1 if module.has_requirements else 0,
            "antivirus_status": module.antivirus_status,
            "error": module.error,
        },
        preserve_existing=True,
        event_type="module.sync",
    )
    upsert(
        "module_sources",
        "source_id",
        module.module_key,
        {
            "module_key": module.module_key,
            "source_type": "folder" if Path(module.path).is_dir() else "file",
            "url": module.source_url,
            "path": module.path,
            "trusted": 1 if module.antivirus_status == "trusted" else 0,
        },
        preserve_existing=True,
        event_type="module.source.sync",
    )
    for req in module.requirements or []:
        upsert(
            "module_requirements",
            "module_key",
            module.module_key,
            {"requirement": req, "installed": 0, "error": ""},
            preserve_existing=True,
            event_type="module.requirement.sync",
        )


def sync_installed_modules() -> list[ModuleState]:
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    meta = _read_meta()
    modules: list[ModuleState] = []
    errors: list[str] = []
    for path in sorted(MODULES_DIR.iterdir(), key=lambda item: item.name.lower()):
        state = inspect_module_path(path, meta)
        if not state:
            continue
        modules.append(state)
        sync_module_state(state)
        if state.error:
            errors.append(f"{state.module_key}: {state.error[:180]}")
    set_health(
        "modules",
        "ok" if not errors else "warning",
        f"Modules synced: {len(modules)}" if not errors else f"Modules synced with {len(errors)} issue(s)",
        {"modules": [asdict(item) for item in modules], "errors": errors},
    )
    return modules


def mark_module_runtime(module_key: str, *, status: str, error: str = "") -> None:
    upsert(
        "modules",
        "module_key",
        module_key,
        {"status": status, "error": error, "enabled": 1 if status == "loaded" else 0},
        preserve_existing=True,
        event_type="module.runtime",
    )
