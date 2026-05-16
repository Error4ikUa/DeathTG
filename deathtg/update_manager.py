from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from deathtg.config import ROOT_DIR, RUNTIME_DIR


UPDATE_STATE_PATH = RUNTIME_DIR / "update_state.json"


def _run_git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT_DIR,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _output(result: subprocess.CompletedProcess[str]) -> str:
    text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    return text or "No output"


def save_update_state(payload: dict[str, object]) -> dict[str, object]:
    current = load_update_state() or {}
    current.update({key: value for key, value in payload.items() if value is not None})
    current.setdefault("checked_at", int(time.time()))
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    UPDATE_STATE_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def load_update_state() -> dict[str, object] | None:
    if not UPDATE_STATE_PATH.exists():
        return None
    try:
        data = json.loads(UPDATE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def update_notify_enabled() -> bool:
    raw = (os.getenv("UPDATE_NOTIFY", "1") or "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def update_notify_interval() -> int:
    raw = (os.getenv("UPDATE_NOTIFY_INTERVAL", "3600") or "3600").strip()
    try:
        value = int(raw)
    except Exception:
        return 3600
    return max(300, min(value, 60 * 60 * 24))


def inspect_update() -> dict[str, object]:
    branch_result = _run_git("branch", "--show-current", timeout=20)
    branch = (branch_result.stdout or "").strip() or "main"
    fetch_result = _run_git("fetch", "--all", "--prune", timeout=180)
    if fetch_result.returncode != 0:
        return {
            "ok": False,
            "branch": branch,
            "update_available": False,
            "message": _output(fetch_result)[-3000:],
            "checked_at": int(time.time()),
        }

    local_result = _run_git("rev-parse", "HEAD", timeout=20)
    remote_result = _run_git("rev-parse", f"origin/{branch}", timeout=20)
    if local_result.returncode != 0 or remote_result.returncode != 0:
        failing = local_result if local_result.returncode != 0 else remote_result
        return {
            "ok": False,
            "branch": branch,
            "update_available": False,
            "message": _output(failing)[-3000:],
            "checked_at": int(time.time()),
        }

    current = (local_result.stdout or "").strip()
    upcoming = (remote_result.stdout or "").strip()
    behind_result = _run_git("rev-list", "--count", f"HEAD..origin/{branch}", timeout=20)
    ahead_result = _run_git("rev-list", "--count", f"origin/{branch}..HEAD", timeout=20)
    behind = int((behind_result.stdout or "0").strip() or 0) if behind_result.returncode == 0 else 0
    ahead = int((ahead_result.stdout or "0").strip() or 0) if ahead_result.returncode == 0 else 0
    update_available = bool(behind > 0 and current != upcoming)
    return {
        "ok": True,
        "branch": branch,
        "current": current,
        "upcoming": upcoming,
        "update_available": update_available,
        "behind": behind,
        "ahead": ahead,
        "message": "Update available" if update_available else "Already up to date",
        "checked_at": int(time.time()),
    }


def should_notify_update(info: dict[str, object], state: dict[str, object] | None = None) -> bool:
    if not info.get("ok") or not info.get("update_available"):
        return False
    state = state or load_update_state() or {}
    target = str(info.get("upcoming") or "")
    if not target:
        return False
    if str(state.get("ignored_upcoming") or "") == target:
        return False
    if str(state.get("notified_upcoming") or "") == target:
        return False
    return True


def mark_update_notified(info: dict[str, object]) -> dict[str, object]:
    return save_update_state(
        {
            **info,
            "notified_upcoming": str(info.get("upcoming") or ""),
            "notified_at": int(time.time()),
        }
    )


def ignore_update(info: dict[str, object]) -> dict[str, object]:
    return save_update_state(
        {
            **info,
            "ignored_upcoming": str(info.get("upcoming") or ""),
            "ignored_at": int(time.time()),
        }
    )


def clear_ignored_update() -> dict[str, object]:
    state = load_update_state() or {}
    state.pop("ignored_upcoming", None)
    state.pop("ignored_at", None)
    return save_update_state(state)


def apply_update() -> dict[str, object]:
    before = _run_git("rev-parse", "HEAD", timeout=20)
    before_sha = (before.stdout or "").strip() if before.returncode == 0 else ""
    pull_result = _run_git("pull", "--ff-only", timeout=240)
    message = _output(pull_result)[-3500:]
    if pull_result.returncode != 0:
        result = {"ok": False, "updated": False, "restart_required": False, "message": message}
        save_update_state(result)
        return result

    after = _run_git("rev-parse", "HEAD", timeout=20)
    after_sha = (after.stdout or "").strip() if after.returncode == 0 else ""
    changed = bool(before_sha and after_sha and before_sha != after_sha)
    requirements_changed = False
    if changed:
        diff_result = _run_git("diff", "--name-only", before_sha, after_sha, timeout=30)
        changed_files = {(line or "").strip() for line in (diff_result.stdout or "").splitlines() if line.strip()}
        requirements_changed = "requirements.txt" in changed_files
        if requirements_changed:
            pip_result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(ROOT_DIR / "requirements.txt")],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                timeout=300,
            )
            pip_output = _output(pip_result)[-2000:]
            message = f"{message}\n\nRequirements:\n{pip_output}"
            if pip_result.returncode != 0:
                result = {
                    "ok": False,
                    "updated": changed,
                    "restart_required": False,
                    "message": message,
                }
                save_update_state(result)
                return result

    result = {
        "ok": True,
        "updated": changed,
        "restart_required": changed,
        "requirements_changed": requirements_changed,
        "message": message,
        "ignored_upcoming": "",
        "notified_upcoming": "",
        "applied_at": int(time.time()),
    }
    save_update_state(result)
    return result


def schedule_restart(delay: float = 1.0) -> None:
    def _restart_worker() -> None:
        time.sleep(max(0.2, delay))
        try:
            import dtg

            dtg.supervisor_stop.set()
            dtg.stop_userbot()
        except Exception:
            pass
        os.chdir(ROOT_DIR)
        os.execv(sys.executable, [sys.executable, str(ROOT_DIR / "dtg.py")])

    thread = threading.Thread(target=_restart_worker, name="deathtg-restart", daemon=True)
    thread.start()
