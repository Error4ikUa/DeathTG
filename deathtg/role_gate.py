from __future__ import annotations

import json
import os
from pathlib import Path

from deathtg.config import RUNTIME_DIR


OWNER_TG_ID = 2054091032
ADMIN_KEY = "qi2m3of9n5"
DEVELOPER_KEY = "orkfvimd"
VALID_ROLES = {"user", "admin", "developer"}


def current_owner_id() -> int | None:
    raw_owner = (os.getenv("OWNER_ID", "") or "").strip()
    if raw_owner.isdigit():
        return int(raw_owner)
    profile_path = RUNTIME_DIR / "profile.json"
    if not profile_path.exists():
        return None
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    raw_id = str(data.get("id") or "").strip()
    return int(raw_id) if raw_id.isdigit() else None


def normalize_role(role: str) -> str:
    value = (role or "").strip().lower()
    return value if value in VALID_ROLES else "user"


def can_assign_role(*, current_role: str, requested_role: str, provided_key: str = "") -> tuple[bool, str]:
    current = normalize_role(current_role)
    requested = normalize_role(requested_role)
    if requested == current:
        return True, ""
    if requested == "user":
        return True, ""
    owner_id = current_owner_id()
    if owner_id == OWNER_TG_ID:
        return True, ""
    expected = ADMIN_KEY if requested == "admin" else DEVELOPER_KEY
    if (provided_key or "").strip() == expected:
        return True, ""
    if requested == "admin":
        return False, "Admin key required."
    return False, "Developer key required."
