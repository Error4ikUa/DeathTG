from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from deathtg.config import ENV_PATH, ROOT_DIR, RUNTIME_DIR
from deathtg.server_bootstrap import parse_env_file, update_env_values

SESSION_BACKUP_DIR = RUNTIME_DIR / "session_backups"
UPDATE_MARKER_PATH = SESSION_BACKUP_DIR / "latest_update.json"
UPDATE_MARKER_TTL = 24 * 60 * 60


def _safe_reason(reason: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", reason.strip() or "snapshot")[:48]


def _snapshot_dir(reason: str) -> Path:
    return SESSION_BACKUP_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}_{_safe_reason(reason)}"


def _session_base(session_name: str) -> Path:
    raw = (session_name or "deathtg").strip() or "deathtg"
    if raw.endswith(".session"):
        raw = raw[: -len(".session")]
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def session_main_file(session_name: str) -> Path:
    base = _session_base(session_name)
    return base.with_name(f"{base.name}.session")


def session_files(session_name: str) -> list[Path]:
    main = session_main_file(session_name)
    files = [
        path
        for path in main.parent.glob(f"{main.name}*")
        if path.is_file() and not path.name.endswith(".invalid")
    ]
    return sorted(files)


def current_session_name() -> str:
    env = parse_env_file(ENV_PATH)
    return (env.get("SESSION_NAME") or os.getenv("SESSION_NAME") or "deathtg").strip() or "deathtg"


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except Exception:
        return path.name


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _bot_session_files() -> list[Path]:
    if not RUNTIME_DIR.exists():
        return []
    return sorted(
        path
        for pattern in ("*_bot.session*", "inline_bot.session*", "helper_bot.session*", "community_bot.session*")
        for path in RUNTIME_DIR.glob(pattern)
        if path.is_file() and SESSION_BACKUP_DIR not in path.parents
    )


def private_runtime_files(session_name: str | None = None) -> list[Path]:
    name = session_name or current_session_name()
    candidates: list[Path] = []
    candidates.extend(session_files(name))
    candidates.extend(_bot_session_files())
    for path in (ENV_PATH, ROOT_DIR / "config.json"):
        if path.exists() and path.is_file():
            candidates.append(path)
    dedup: dict[Path, Path] = {}
    for path in candidates:
        try:
            dedup[path.resolve()] = path
        except Exception:
            dedup[path] = path
    return list(dedup.values())


def create_private_snapshot(reason: str = "snapshot", *, mark_update: bool = False) -> dict[str, Any]:
    session_name = current_session_name()
    files = private_runtime_files(session_name)
    SESSION_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    backup_dir = _snapshot_dir(reason)
    manifest: dict[str, Any] = {
        "created_at": stamp,
        "reason": reason,
        "session_name": session_name,
        "files": [],
    }
    if files:
        backup_dir.mkdir(parents=True, exist_ok=True)
    for source in files:
        relative = _relative(source)
        target = backup_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        _chmod_private(target)
        manifest["files"].append({"source": relative, "backup": relative})
    if files:
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _chmod_private(backup_dir / "manifest.json")
    if mark_update:
        UPDATE_MARKER_PATH.write_text(
            json.dumps({"backup_dir": str(backup_dir), **manifest}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _chmod_private(UPDATE_MARKER_PATH)
    return {
        "ok": True,
        "backup_dir": str(backup_dir) if files else "",
        "count": len(files),
        "session_name": session_name,
    }


def backup_session_files(session_name: str, reason: str = "session") -> dict[str, Any]:
    files = session_files(session_name)
    if not files:
        return {"ok": True, "backup_dir": "", "count": 0, "session_name": session_name}
    SESSION_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_dir = _snapshot_dir(reason)
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": int(time.time()), "reason": reason, "session_name": session_name, "files": []}
    for source in files:
        relative = _relative(source)
        target = backup_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        _chmod_private(target)
        manifest["files"].append({"source": relative, "backup": relative})
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _chmod_private(backup_dir / "manifest.json")
    return {"ok": True, "backup_dir": str(backup_dir), "count": len(files), "session_name": session_name}


def _load_manifest(backup_dir: Path) -> dict[str, Any] | None:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def restore_private_snapshot(backup_dir: str | Path, *, overwrite: bool = False) -> dict[str, Any]:
    base = Path(backup_dir)
    manifest = _load_manifest(base)
    if not manifest:
        return {"ok": False, "restored": 0, "message": "session snapshot manifest is missing"}
    restored = 0
    skipped = 0
    for item in manifest.get("files", []):
        if not isinstance(item, dict):
            continue
        source_rel = str(item.get("source") or "").strip()
        backup_rel = str(item.get("backup") or source_rel).strip()
        if not source_rel or not backup_rel:
            continue
        source = base / backup_rel
        target = ROOT_DIR / source_rel
        if not source.exists() or not source.is_file():
            skipped += 1
            continue
        if target.exists() and not overwrite:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        _chmod_private(target)
        restored += 1
    return {"ok": True, "restored": restored, "skipped": skipped, "backup_dir": str(base)}


def protect_update_session_snapshot() -> dict[str, Any]:
    return create_private_snapshot("update", mark_update=True)


def _load_update_marker() -> dict[str, Any] | None:
    if not UPDATE_MARKER_PATH.exists():
        return None
    try:
        data = json.loads(UPDATE_MARKER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _clear_update_marker() -> None:
    try:
        UPDATE_MARKER_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def recover_update_session_snapshot(*, clear: bool = True) -> dict[str, Any]:
    marker = _load_update_marker()
    if not marker:
        return {"ok": True, "restored": 0, "message": "no update session snapshot"}
    created_at = int(marker.get("created_at") or 0)
    if created_at and time.time() - created_at > UPDATE_MARKER_TTL:
        if clear:
            _clear_update_marker()
        return {"ok": True, "restored": 0, "message": "update session snapshot expired"}
    backup_dir = str(marker.get("backup_dir") or "")
    result = restore_private_snapshot(backup_dir, overwrite=False) if backup_dir else {"ok": True, "restored": 0}
    if clear:
        _clear_update_marker()
    return result


def migrate_legacy_session_if_needed(session_name: str | None = None) -> dict[str, Any]:
    desired = (session_name or current_session_name()).strip() or "deathtg"
    expected = session_main_file(desired)
    if expected.exists():
        return {"ok": True, "changed": False, "message": "session is present"}

    root_sessions = sorted(path for path in ROOT_DIR.glob("*.session") if path.is_file())
    if not root_sessions:
        return {"ok": True, "changed": False, "message": "no legacy session candidates"}

    selected: Path | None = None
    legacy_default = ROOT_DIR / "deathtg.session"
    if desired != "deathtg" and legacy_default.exists():
        selected = legacy_default
    elif len(root_sessions) == 1:
        selected = root_sessions[0]
        adopted_name = selected.name[: -len(".session")]
        if adopted_name and adopted_name != desired:
            update_env_values({"SESSION_NAME": adopted_name}, path=ENV_PATH)
            desired = adopted_name
            expected = session_main_file(desired)

    if selected is None:
        return {"ok": True, "changed": False, "message": "multiple legacy sessions, no automatic migration"}

    source_base = selected.name[: -len(".session")]
    source_files = session_files(source_base)
    if not source_files:
        return {"ok": True, "changed": False, "message": "legacy session vanished before migration"}

    restored = 0
    for source in source_files:
        suffix = source.name[len(source_base) :]
        target = expected.with_name(f"{expected.stem}{suffix}")
        if target.exists():
            continue
        shutil.copy2(source, target)
        _chmod_private(target)
        restored += 1
    return {
        "ok": True,
        "changed": bool(restored),
        "restored": restored,
        "message": f"migrated legacy session {source_base} -> {desired}" if restored else "session already migrated",
    }


def ensure_session_available(session_name: str | None = None) -> dict[str, Any]:
    update_result = recover_update_session_snapshot(clear=True)
    migrate_result = migrate_legacy_session_if_needed(session_name)
    return {
        "ok": bool(update_result.get("ok", True) and migrate_result.get("ok", True)),
        "update": update_result,
        "migration": migrate_result,
        "changed": bool(update_result.get("restored") or migrate_result.get("changed")),
    }
