from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from deathtg.config import ENV_PATH, RUNTIME_DIR
from deathtg.session_guard import session_main_file


STARTUP_STATE_PATH = RUNTIME_DIR / "startup_state.json"
STARTUP_STATUS_PATH = RUNTIME_DIR / "startup_status.json"
HEALTH_STATE_PATH = RUNTIME_DIR / "health_state.json"

PHASE_FIRST_RUN = "FIRST_RUN"
PHASE_SETUP_WAIT_QR = "SETUP_WAIT_QR"
PHASE_SETUP_WAIT_2FA = "SETUP_WAIT_2FA"
PHASE_POST_SETUP_SYNC = "POST_SETUP_SYNC"
PHASE_READY = "READY"
PHASE_DEGRADED = "DEGRADED"
PHASE_REPAIR = "REPAIR"
PHASE_SAFE_MODE = "SAFE_MODE"

TRANSIENT_RUNTIME_PHASES = {PHASE_POST_SETUP_SYNC, PHASE_REPAIR}
SESSION_INVALID_MARKERS = ("session is missing", "session is missing or invalid", "session file is missing", "session invalid")
SETUP_QR_STAGES = {
    "",
    "idle",
    "starting",
    "waiting_qr",
    "qr_confirmed",
    "qr_expired",
    "qr_error",
}
SETUP_2FA_STAGES = {"waiting_2fa", "2fa_confirmed"}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _env_values() -> dict[str, str]:
    if ENV_PATH.exists():
        values = dotenv_values(ENV_PATH)
        return {str(k): str(v or "") for k, v in values.items() if k is not None}
    return {}


def _env_flag(values: dict[str, str], key: str) -> bool:
    return str(values.get(key, "") or "").strip().lower() in {"1", "true", "yes", "on"}


def _session_path(values: dict[str, str]) -> Path:
    session_name = str(values.get("SESSION_NAME") or "deathtg").strip() or "deathtg"
    return session_main_file(session_name)


def _has_env(values: dict[str, str]) -> bool:
    return bool(str(values.get("API_ID") or "").strip() and str(values.get("API_HASH") or "").strip())


def _integrity_failures(startup_status: dict, health_state: dict) -> list[str]:
    failures: list[str] = []
    for item in list(startup_status.get("bots") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("role") or "bot")
        if item.get("error"):
            failures.append(f"{label}: {item.get('error')}")
            continue
        if not item.get("configured"):
            failures.append(f"{label}: missing token")
        elif not item.get("valid_username"):
            failures.append(f"{label}: invalid username")
        elif not item.get("start_ping"):
            failures.append(f"{label}: start ping failed")
    folder = startup_status.get("folder") if isinstance(startup_status.get("folder"), dict) else {}
    if folder and not folder.get("ok") and folder.get("error"):
        failures.append(f"folder: {folder.get('error')}")
    action = health_state.get("last_action") if isinstance(health_state.get("last_action"), dict) else {}
    if action and action.get("ok") is False:
        failures.append(str(action.get("message") or "health action failed"))
    return failures


def write_startup_state(phase: str, message: str = "", **extra) -> dict:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": phase,
        "message": message.strip(),
        "updated_at": int(time.time()),
        **extra,
    }
    STARTUP_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def clear_startup_state() -> None:
    with suppress(FileNotFoundError):
        STARTUP_STATE_PATH.unlink()


def load_startup_state() -> dict:
    return _load_json(STARTUP_STATE_PATH)


def startup_snapshot() -> dict:
    load_dotenv(ENV_PATH, override=True)
    env = _env_values()
    has_env = _has_env(env)
    login_pending = _env_flag(env, "LOGIN_PENDING")
    login_stage = str(env.get("LOGIN_STAGE") or "idle").strip().lower() or "idle"
    has_session = _session_path(env).exists()
    safe_mode = _env_flag(env, "DTG_SAFE_MODE")

    runtime_state = load_startup_state()
    startup_status = _load_json(STARTUP_STATUS_PATH)
    health_state = _load_json(HEALTH_STATE_PATH)

    phase = PHASE_READY
    message = ""

    if not has_env:
        phase = PHASE_FIRST_RUN
        message = "API credentials are missing. Open setup to connect Telegram."
    elif login_pending:
        if login_stage in SETUP_2FA_STAGES:
            phase = PHASE_SETUP_WAIT_2FA
            message = "Telegram asked for the 2FA password."
        else:
            phase = PHASE_SETUP_WAIT_QR
            message = "DeathTG is waiting for Telegram QR approval."
    elif not has_session:
        phase = PHASE_FIRST_RUN
        message = "Telegram session is missing. Run setup to create a session."
    elif safe_mode:
        phase = PHASE_SAFE_MODE
        message = "DeathTG is running in safe mode."
    else:
        runtime_phase = str(runtime_state.get("phase") or "").strip().upper()
        runtime_message = str(runtime_state.get("message") or "").strip()
        runtime_message_l = runtime_message.lower()
        updated_at = int(runtime_state.get("updated_at") or 0)
        age_seconds = max(0, int(time.time()) - updated_at) if updated_at else None
        failures = _integrity_failures(startup_status, health_state)
        if runtime_phase == PHASE_DEGRADED and any(marker in runtime_message_l for marker in SESSION_INVALID_MARKERS):
            phase = PHASE_FIRST_RUN
            message = runtime_message or "Telegram session is missing or invalid. Run setup to reconnect."
        elif runtime_phase in TRANSIENT_RUNTIME_PHASES and age_seconds is not None and age_seconds <= 600:
            phase = runtime_phase
            message = runtime_message or ("DeathTG is repairing startup state." if runtime_phase == PHASE_REPAIR else "DeathTG is finishing post-setup sync.")
        elif failures:
            phase = PHASE_DEGRADED
            message = failures[0]
        else:
            phase = PHASE_READY
            message = runtime_message or "Panel and userbot are ready."

    return {
        "phase": phase,
        "message": message,
        "has_env": has_env,
        "has_session": has_session,
        "login_pending": login_pending,
        "login_stage": login_stage,
        "safe_mode": safe_mode,
        "userbot_ready": has_env and has_session and not login_pending,
        "setup_required": phase in {PHASE_FIRST_RUN, PHASE_SETUP_WAIT_QR, PHASE_SETUP_WAIT_2FA},
        "runtime_state": runtime_state,
        "startup_status": startup_status,
        "health_state": health_state,
    }


def sync_startup_state() -> dict:
    snapshot = startup_snapshot()
    return write_startup_state(
        snapshot["phase"],
        str(snapshot.get("message") or ""),
        has_env=bool(snapshot.get("has_env")),
        has_session=bool(snapshot.get("has_session")),
        login_pending=bool(snapshot.get("login_pending")),
        login_stage=str(snapshot.get("login_stage") or "idle"),
        safe_mode=bool(snapshot.get("safe_mode")),
        userbot_ready=bool(snapshot.get("userbot_ready")),
    )
