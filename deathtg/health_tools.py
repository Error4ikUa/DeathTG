from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from deathtg.assets import resolve_module_entry
from deathtg.config import MODULES_DIR, ROOT_DIR, RUNTIME_DIR
from deathtg.module_repo import parse_requirements_text
from deathtg.profile_store import update_env_value
from deathtg.security import is_trusted_module_link, scan_module_source


HEALTH_STATE_PATH = RUNTIME_DIR / "health_state.json"
HEALTH_EXPORTS_DIR = RUNTIME_DIR / "health_exports"
MODULE_META_PATH = RUNTIME_DIR / "module_meta.json"
SAFE_MODE_ENV_KEY = "DTG_SAFE_MODE"
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def load_health_state() -> dict[str, object]:
    if not HEALTH_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(HEALTH_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_health_state(**updates: object) -> dict[str, object]:
    current = load_health_state()
    current.update({key: value for key, value in updates.items() if value is not None})
    current["updated_at"] = int(time.time())
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_STATE_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def safe_mode_enabled() -> bool:
    raw = (os.getenv(SAFE_MODE_ENV_KEY, "0") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def set_safe_mode(enabled: bool) -> None:
    update_env_value(SAFE_MODE_ENV_KEY, "1" if enabled else "0")
    os.environ[SAFE_MODE_ENV_KEY] = "1" if enabled else "0"
    save_health_state(safe_mode=enabled)


def _load_module_meta() -> dict[str, dict]:
    if not MODULE_META_PATH.exists():
        return {}
    try:
        data = json.loads(MODULE_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_module_meta(data: dict[str, dict]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    MODULE_META_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _iter_local_modules() -> list[tuple[str, Path]]:
    modules: list[tuple[str, Path]] = []
    if not MODULES_DIR.exists():
        return modules
    for item in sorted(MODULES_DIR.iterdir(), key=lambda path: path.name.lower()):
        if item.name.startswith((".", "_")):
            continue
        if item.is_file() and item.suffix.lower() == ".py":
            modules.append((item.stem, item))
            continue
        if item.is_dir():
            entry = resolve_module_entry(item, item.name)
            if entry and entry.exists():
                modules.append((item.name, entry))
    return modules


def _requirements_from_entry(entry: Path) -> list[str]:
    source = ""
    try:
        source = entry.read_text(encoding="utf-8", errors="replace")
    except Exception:
        source = ""
    requirements: list[str] = []
    for line in source.splitlines()[:120]:
        match = re.match(r"#\s*requires:\s*(.+)$", line.strip(), flags=re.I)
        if match:
            requirements.extend(part.strip() for part in match.group(1).split() if part.strip())
    requirements_file = entry.parent / "requirements.txt"
    if requirements_file.exists():
        try:
            requirements.extend(parse_requirements_text(requirements_file.read_text(encoding="utf-8", errors="replace")))
        except Exception:
            pass
    return sorted({item.strip() for item in requirements if item and item.strip()})


def _distribution_name(requirement: str) -> str:
    match = REQUIREMENT_NAME_RE.match(requirement or "")
    if not match:
        return ""
    return match.group(1).strip()


def _requirement_installed(requirement: str) -> bool:
    name = _distribution_name(requirement)
    if not name:
        return True
    try:
        importlib.metadata.version(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False
    except Exception:
        return False


def collect_requirement_state() -> dict[str, object]:
    modules: list[dict[str, object]] = []
    missing_unique: list[str] = []
    missing_seen: set[str] = set()
    for module_name, entry in _iter_local_modules():
        requirements = _requirements_from_entry(entry)
        missing = [item for item in requirements if not _requirement_installed(item)]
        for item in missing:
            lowered = item.lower()
            if lowered not in missing_seen:
                missing_seen.add(lowered)
                missing_unique.append(item)
        modules.append(
            {
                "name": module_name,
                "entry": str(entry),
                "requirements": requirements,
                "missing": missing,
            }
        )
    return {
        "modules": modules,
        "missing": missing_unique,
        "missing_count": len(missing_unique),
        "affected_modules": sum(1 for item in modules if item["missing"]),
    }


def install_missing_requirements() -> dict[str, object]:
    state = collect_requirement_state()
    missing = list(state.get("missing") or [])
    if not missing:
        result = {"ok": True, "installed": [], "message": "No missing requirements found."}
        save_health_state(last_requirements=result, requirements_state=state)
        return result
    process = subprocess.run(
        [sys.executable, "-m", "pip", "install", *missing],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        timeout=600,
    )
    output = ((process.stdout or "") + "\n" + (process.stderr or "")).strip()[-4000:]
    result = {
        "ok": process.returncode == 0,
        "installed": missing if process.returncode == 0 else [],
        "message": output or ("Requirements installed." if process.returncode == 0 else "pip failed"),
    }
    save_health_state(last_requirements=result, requirements_state=collect_requirement_state())
    return result


def scan_local_modules() -> dict[str, object]:
    meta = _load_module_meta()
    modules: list[dict[str, object]] = []
    summary = {"clean": 0, "warning": 0, "danger": 0, "trusted": 0}
    for module_name, entry in _iter_local_modules():
        try:
            source = entry.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            modules.append({"name": module_name, "ok": False, "message": str(exc), "severity": "danger"})
            summary["danger"] += 1
            continue
        module_meta = dict(meta.get(module_name) or {})
        trusted_link = str(module_meta.get("source_url") or module_meta.get("source_link") or "")
        report = scan_module_source(source, trusted=is_trusted_module_link(trusted_link))
        module_meta.update(
            {
                "security_verdict": report.verdict,
                "security_score": report.score,
                "security_findings": [
                    {"line": item.line, "reason": item.reason, "score": item.score, "code": item.code}
                    for item in report.findings
                ],
                "security_override": bool(module_meta.get("security_override")),
                "updated_at": int(time.time()),
            }
        )
        meta[module_name] = module_meta
        if report.severity == "warning":
            summary["warning"] += 1
        elif report.severity == "danger":
            summary["danger"] += 1
        elif report.trusted:
            summary["trusted"] += 1
        else:
            summary["clean"] += 1
        modules.append(
            {
                "name": module_name,
                "ok": report.allowed,
                "severity": report.severity,
                "verdict": report.verdict,
                "score": report.score,
                "message": report.pretty(),
            }
        )
    _save_module_meta(meta)
    result = {"ok": True, "summary": summary, "modules": modules}
    save_health_state(last_scan=result)
    return result


def export_logs_bundle() -> Path:
    HEALTH_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = HEALTH_EXPORTS_DIR / f"deathtg-health-{int(time.time())}.zip"
    include_files = [
        RUNTIME_DIR / "deathtg.log",
        RUNTIME_DIR / "startup_status.json",
        RUNTIME_DIR / "health_state.json",
        RUNTIME_DIR / "profile.json",
        RUNTIME_DIR / "profile_settings.json",
        RUNTIME_DIR / "module_meta.json",
        RUNTIME_DIR / "panel_actions.jsonl",
        RUNTIME_DIR / "update_state.json",
    ]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include_files:
            if path.exists() and path.is_file():
                archive.write(path, arcname=path.name)
    save_health_state(last_export=str(target))
    return target
