from __future__ import annotations

import json
import os
import time
from pathlib import Path

from deathtg.config import RUNTIME_DIR
from deathtg.role_gate import OWNER_TG_ID, normalize_role

COMMUNITY_REGISTRY_PATH = RUNTIME_DIR / "community_roles.json"
ROLE_SCAN_RESULTS_DIR = RUNTIME_DIR / "role_scan_results"
DEFAULT_COMMUNITY_BOT_USERNAME = "Djdkxkxyscomunity_bot"


def _normalize_registry(data: object) -> dict[str, dict]:
    if not isinstance(data, dict):
        return {}
    result: dict[str, dict] = {}
    for raw_user_id, raw_item in data.items():
        user_id = str(raw_user_id).strip()
        if not user_id.isdigit() or not isinstance(raw_item, dict):
            continue
        roles = [normalize_role(item) for item in raw_item.get("roles", []) if normalize_role(item) != "user"]
        result[user_id] = {
            "roles": sorted(set(roles)),
            "updated_at": int(raw_item.get("updated_at", 0) or 0),
            "updated_by": str(raw_item.get("updated_by") or "").strip(),
            "updated_by_name": str(raw_item.get("updated_by_name") or "").strip(),
        }
    return result


def load_role_registry() -> dict[str, dict]:
    if not COMMUNITY_REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(COMMUNITY_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return _normalize_registry(data)


def save_role_registry(data: dict[str, dict]) -> dict[str, dict]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_registry(data)
    COMMUNITY_REGISTRY_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def preferred_community_bot_username() -> str:
    raw = (os.getenv("COMMUNITY_BOT_USERNAME", "") or "").strip().lstrip("@")
    username = raw or DEFAULT_COMMUNITY_BOT_USERNAME
    if not username.lower().endswith("bot"):
        username = f"{username}_bot"
    return username


def community_enabled_for_owner(owner_id: int | None) -> bool:
    return int(owner_id or 0) == OWNER_TG_ID


def community_bot_display_name() -> str:
    return "DeathTG Community"


def grant_role(user_id: int, role: str, *, actor_id: int | None = None, actor_name: str = "") -> dict[str, dict]:
    normalized_role = normalize_role(role)
    if normalized_role == "user":
        return load_role_registry()
    registry = load_role_registry()
    key = str(int(user_id))
    item = dict(registry.get(key) or {})
    roles = {normalize_role(entry) for entry in item.get("roles", [])}
    roles.discard("user")
    roles.add(normalized_role)
    item.update(
        {
            "roles": sorted(roles),
            "updated_at": int(time.time()),
            "updated_by": str(int(actor_id)) if actor_id else "",
            "updated_by_name": (actor_name or "").strip(),
        }
    )
    registry[key] = item
    return save_role_registry(registry)


def revoke_role(user_id: int, role: str) -> dict[str, dict]:
    normalized_role = normalize_role(role)
    registry = load_role_registry()
    key = str(int(user_id))
    item = dict(registry.get(key) or {})
    roles = {normalize_role(entry) for entry in item.get("roles", [])}
    roles.discard(normalized_role)
    roles.discard("user")
    if roles:
        item["roles"] = sorted(roles)
        item["updated_at"] = int(time.time())
        registry[key] = item
    else:
        registry.pop(key, None)
    return save_role_registry(registry)


def list_role_entries() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw_user_id, item in load_role_registry().items():
        roles = [normalize_role(role) for role in item.get("roles", []) if normalize_role(role) != "user"]
        if not roles:
            continue
        result.append(
            {
                "user_id": int(raw_user_id),
                "roles": roles,
                "updated_at": int(item.get("updated_at", 0) or 0),
                "updated_by": str(item.get("updated_by") or "").strip(),
                "updated_by_name": str(item.get("updated_by_name") or "").strip(),
            }
        )
    return sorted(result, key=lambda row: int(row["user_id"]))


def allowed_role(user_id: int, role: str) -> bool:
    normalized_role = normalize_role(role)
    if normalized_role == "user":
        return True
    item = load_role_registry().get(str(int(user_id))) or {}
    roles = {normalize_role(entry) for entry in item.get("roles", [])}
    return normalized_role in roles


def role_scan_result_path(request_id: str) -> Path:
    safe = "".join(ch for ch in str(request_id) if ch.isalnum() or ch in {"_", "-"})
    return ROLE_SCAN_RESULTS_DIR / f"{safe}.json"


def write_role_scan_result(request_id: str, *, ok: bool, message: str = "", role: str = "") -> None:
    ROLE_SCAN_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    role_scan_result_path(request_id).write_text(
        json.dumps(
            {
                "ok": bool(ok),
                "message": str(message or ""),
                "role": normalize_role(role),
                "ts": int(time.time()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def read_role_scan_result(request_id: str) -> dict[str, object] | None:
    path = role_scan_result_path(request_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def clear_role_scan_result(request_id: str) -> None:
    try:
        role_scan_result_path(request_id).unlink(missing_ok=True)
    except Exception:
        pass
