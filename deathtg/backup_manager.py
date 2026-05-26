from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path
from typing import Any

from deathtg.config import MODULES_DIR, RUNTIME_DIR


BACKUP_DIR = RUNTIME_DIR / "backups"
BACKUP_SUFFIX = ".dtgbak"
MANIFEST_NAME = "manifest.json"
SKIP_NAMES = {"__pycache__", ".git", ".venv", "venv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".log"}


def _safe_arcname(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _iter_module_files() -> list[Path]:
    if not MODULES_DIR.exists():
        return []
    files: list[Path] = []
    for path in sorted(MODULES_DIR.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_NAMES for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append(path)
    return files


def _module_names(files: list[Path]) -> list[str]:
    names: set[str] = set()
    for path in files:
        try:
            rel = path.resolve().relative_to(MODULES_DIR.resolve())
        except Exception:
            continue
        first = rel.parts[0] if rel.parts else ""
        if first.endswith(".py"):
            first = Path(first).stem
        if first:
            names.add(first)
    return sorted(names, key=str.lower)


def create_modules_backup(reason: str = "manual") -> dict[str, Any]:
    """Create a portable modules-only DeathTG backup.

    The archive intentionally skips secrets and sessions. It is meant for the
    "reinstall from zero, restore my modules" flow.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = _iter_module_files()
    module_names = _module_names(files)
    stamp = int(time.time())
    filename = f"DeathTG_modules_{time.strftime('%Y%m%d_%H%M%S')}{BACKUP_SUFFIX}"
    target = BACKUP_DIR / filename
    manifest = {
        "format": "DeathTG modules backup",
        "version": 1,
        "created_at": stamp,
        "reason": reason,
        "modules": module_names,
        "files": [],
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in files:
            arcname = "modules/" + _safe_arcname(source, MODULES_DIR)
            archive.write(source, arcname=arcname)
            manifest["files"].append(arcname)
        archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    return {
        "ok": True,
        "path": str(target),
        "filename": filename,
        "modules": module_names,
        "module_count": len(module_names),
        "file_count": len(files),
        "created_at": stamp,
    }


def _safe_extract_target(member: str) -> Path | None:
    if not member.startswith("modules/") or member.endswith("/"):
        return None
    relative = Path(member[len("modules/") :])
    if relative.is_absolute() or ".." in relative.parts:
        return None
    if not relative.parts:
        return None
    return MODULES_DIR / relative


def restore_modules_backup(backup_path: str | Path, *, overwrite: bool = True) -> dict[str, Any]:
    source = Path(backup_path)
    if not source.exists() or not source.is_file():
        return {"ok": False, "message": "Backup file does not exist", "restored": 0}
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    restored = 0
    skipped = 0
    modules: set[str] = set()
    with zipfile.ZipFile(source, "r") as archive:
        for member in archive.namelist():
            target = _safe_extract_target(member)
            if target is None:
                continue
            if target.exists() and not overwrite:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
            restored += 1
            first = Path(member[len("modules/") :]).parts[0]
            modules.add(Path(first).stem if first.endswith(".py") else first)
    return {
        "ok": True,
        "path": str(source),
        "restored": restored,
        "skipped": skipped,
        "modules": sorted(modules, key=str.lower),
        "module_count": len(modules),
    }
