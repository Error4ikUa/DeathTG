from __future__ import annotations

import json
import os
import shutil
import stat
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
MAX_BACKUP_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_BACKUP_MEMBERS = 2_000
MAX_BACKUP_MEMBER_BYTES = 32 * 1024 * 1024
MAX_BACKUP_TOTAL_BYTES = 256 * 1024 * 1024
MAX_BACKUP_COMPRESSION_RATIO = 500


def _safe_arcname(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _iter_module_files() -> list[Path]:
    if not MODULES_DIR.exists():
        return []
    files: list[Path] = []
    for path in sorted(MODULES_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
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
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
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


def _validated_members(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, Path]]:
    infos = archive.infolist()
    if len(infos) > MAX_BACKUP_MEMBERS:
        raise ValueError(f"Backup contains too many files ({len(infos)} > {MAX_BACKUP_MEMBERS})")
    total_size = 0
    seen_targets: set[str] = set()
    validated: list[tuple[zipfile.ZipInfo, Path]] = []
    for info in infos:
        if info.is_dir() or info.filename == MANIFEST_NAME:
            continue
        target = _safe_extract_target(info.filename)
        if target is None:
            raise ValueError(f"Backup contains an invalid path: {info.filename!r}")
        if info.flag_bits & 0x1:
            raise ValueError(f"Encrypted backup member is not supported: {info.filename!r}")
        unix_mode = info.external_attr >> 16
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise ValueError(f"Backup contains a symbolic link: {info.filename!r}")
        if info.file_size > MAX_BACKUP_MEMBER_BYTES:
            raise ValueError(f"Backup member is too large: {info.filename!r}")
        total_size += info.file_size
        if total_size > MAX_BACKUP_TOTAL_BYTES:
            raise ValueError("Backup expands beyond the allowed size")
        if info.file_size and (
            info.compress_size <= 0
            or info.file_size / max(1, info.compress_size) > MAX_BACKUP_COMPRESSION_RATIO
        ):
            raise ValueError(f"Suspicious compression ratio in backup member: {info.filename!r}")
        target_key = str(target.resolve()).casefold()
        if target_key in seen_targets:
            raise ValueError(f"Backup contains a duplicate destination: {info.filename!r}")
        seen_targets.add(target_key)
        validated.append((info, target))
    if not validated:
        raise ValueError("Backup does not contain any module files")
    return validated


def restore_modules_backup(backup_path: str | Path, *, overwrite: bool = True) -> dict[str, Any]:
    source = Path(backup_path)
    if not source.exists() or not source.is_file():
        return {"ok": False, "message": "Backup file does not exist", "restored": 0}
    if source.stat().st_size > MAX_BACKUP_ARCHIVE_BYTES:
        return {"ok": False, "message": "Backup archive is too large", "restored": 0}
    if not zipfile.is_zipfile(source):
        return {"ok": False, "message": "Backup is not a valid ZIP archive", "restored": 0}
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    restored = 0
    skipped = 0
    modules: set[str] = set()
    try:
        with zipfile.ZipFile(source, "r") as archive:
            members = _validated_members(archive)
            for info, target in members:
                if target.exists() and not overwrite:
                    skipped += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.dtg-restore-{os.getpid()}.tmp")
                try:
                    with archive.open(info, "r") as source_stream, temporary.open("wb") as target_stream:
                        shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                restored += 1
                first = Path(info.filename[len("modules/") :]).parts[0]
                modules.add(Path(first).stem if first.endswith(".py") else first)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {"ok": False, "message": str(exc), "restored": restored, "skipped": skipped}
    return {
        "ok": True,
        "path": str(source),
        "restored": restored,
        "skipped": skipped,
        "modules": sorted(modules, key=str.lower),
        "module_count": len(modules),
    }
