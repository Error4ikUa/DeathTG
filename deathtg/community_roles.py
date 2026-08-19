from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from deathtg.config import RUNTIME_DIR
from deathtg.role_gate import OWNER_TG_ID, normalize_role


COMMUNITY_REGISTRY_PATH = RUNTIME_DIR / "community_roles.json"
COMMUNITY_ROLES_DB_PATH = RUNTIME_DIR / "community_roles.sqlite3"
ROLE_SCAN_RESULTS_DIR = RUNTIME_DIR / "role_scan_results"
# Public owner authority used by non-owner DeathTG installations.  A local
# instance must never invent a community bot from its own Telegram ID.
DEFAULT_COMMUNITY_BOT_USERNAME = "dtg2054091032_cpnf9hq_bot"
ROLE_CODE_TTL_SECONDS = 15 * 60
ROLE_TITLES = {"admin": "Администратор", "developer": "Разработчик"}
ROLE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _connect() -> sqlite3.Connection:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(COMMUNITY_ROLES_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS role_grants (
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            granted_at INTEGER NOT NULL,
            granted_by INTEGER NOT NULL DEFAULT 0,
            revoked_at INTEGER,
            PRIMARY KEY (user_id, role)
        );
        CREATE TABLE IF NOT EXISTS role_invites (
            code_hash TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            target_user_id INTEGER,
            redeemed_by INTEGER,
            redeemed_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_role_grants_active
            ON role_grants(user_id, revoked_at);
        CREATE INDEX IF NOT EXISTS idx_role_invites_expiry
            ON role_invites(expires_at, redeemed_at);
        """
    )
    _migrate_legacy_registry(conn)
    return conn


@contextmanager
def _database() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _migrate_legacy_registry(conn: sqlite3.Connection) -> None:
    existing = int(conn.execute("SELECT COUNT(*) FROM role_grants").fetchone()[0])
    if existing or not COMMUNITY_REGISTRY_PATH.exists():
        return
    try:
        raw = json.loads(COMMUNITY_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    now = int(time.time())
    for raw_user_id, item in raw.items():
        if not str(raw_user_id).isdigit() or not isinstance(item, dict):
            continue
        for role in item.get("roles", []):
            normalized = normalize_role(str(role))
            if normalized == "user":
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO role_grants
                    (user_id, role, username, display_name, title, granted_at, granted_by, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    int(raw_user_id),
                    normalized,
                    str(item.get("username") or "").lstrip("@"),
                    str(item.get("display_name") or ""),
                    str(item.get("title") or ROLE_TITLES.get(normalized, normalized.title())),
                    int(item.get("updated_at", 0) or now),
                    int(item.get("updated_by", 0) or 0),
                ),
            )
    conn.commit()


def _code_hash(code: str) -> str:
    normalized = "".join(ch for ch in str(code).upper() if ch.isalnum())
    return hashlib.sha256(normalized.encode("ascii", errors="ignore")).hexdigest()


def _new_code(role: str) -> str:
    marker = "ADM" if role == "admin" else "DEV"
    payload = "".join(secrets.choice(ROLE_CODE_ALPHABET) for _ in range(12))
    return f"DTG-{marker}-{payload[:4]}-{payload[4:8]}-{payload[8:]}"


def preferred_community_bot_username(owner_id: int | None = None) -> str:
    configured_owner = str(os.getenv("OWNER_ID", "") or "").strip()
    effective_owner = int(owner_id or configured_owner or 0)
    if effective_owner == OWNER_TG_ID:
        raw = (os.getenv("COMMUNITY_BOT_USERNAME", "") or "").strip().lstrip("@")
    else:
        raw = (os.getenv("DEATHTG_ROLE_BOT_USERNAME", "") or "").strip().lstrip("@")
    username = raw or DEFAULT_COMMUNITY_BOT_USERNAME
    if not username.lower().endswith("bot"):
        username = f"{username}_bot"
    return username


def community_enabled_for_owner(owner_id: int | None) -> bool:
    return int(owner_id or 0) == OWNER_TG_ID


def community_bot_display_name() -> str:
    return "DeathTG Community"


def issue_role_invite(
    role: str,
    *,
    actor_id: int,
    target_user_id: int | None = None,
    ttl_seconds: int = ROLE_CODE_TTL_SECONDS,
) -> dict[str, object]:
    normalized = normalize_role(role)
    if normalized == "user":
        raise ValueError("Only admin and developer invites are supported")
    if int(actor_id) != OWNER_TG_ID:
        raise PermissionError("Only the DeathTG owner can issue role keys")
    now = int(time.time())
    code = _new_code(normalized)
    with _database() as conn:
        conn.execute(
            "DELETE FROM role_invites WHERE expires_at < ? OR redeemed_at IS NOT NULL",
            (now - 24 * 60 * 60,),
        )
        conn.execute(
            """
            INSERT INTO role_invites
                (code_hash, role, created_by, created_at, expires_at, target_user_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _code_hash(code),
                normalized,
                int(actor_id),
                now,
                now + max(60, int(ttl_seconds)),
                int(target_user_id) if target_user_id else None,
            ),
        )
    return {
        "code": code,
        "role": normalized,
        "title": ROLE_TITLES[normalized],
        "expires_at": now + max(60, int(ttl_seconds)),
        "target_user_id": int(target_user_id) if target_user_id else None,
    }


def redeem_role_invite(
    code: str,
    *,
    user_id: int,
    username: str = "",
    display_name: str = "",
) -> dict[str, object]:
    now = int(time.time())
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM role_invites WHERE code_hash=?",
            (_code_hash(code),),
        ).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "message": "Ключ не найден или уже удалён."}
        if int(row["expires_at"] or 0) < now:
            conn.rollback()
            return {"ok": False, "message": "Срок действия ключа истёк. Попросите владельца создать новый."}
        if row["redeemed_at"] is not None:
            conn.rollback()
            return {"ok": False, "message": "Этот ключ уже использован."}
        target_user_id = int(row["target_user_id"] or 0)
        if target_user_id and target_user_id != int(user_id):
            conn.rollback()
            return {"ok": False, "message": "Этот ключ создан для другого пользователя."}
        role = normalize_role(str(row["role"]))
        conn.execute(
            "UPDATE role_invites SET redeemed_by=?, redeemed_at=? WHERE code_hash=? AND redeemed_at IS NULL",
            (int(user_id), now, _code_hash(code)),
        )
        conn.execute(
            """
            INSERT INTO role_grants
                (user_id, role, username, display_name, title, granted_at, granted_by, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_id, role) DO UPDATE SET
                username=excluded.username,
                display_name=excluded.display_name,
                title=excluded.title,
                granted_at=excluded.granted_at,
                granted_by=excluded.granted_by,
                revoked_at=NULL
            """,
            (
                int(user_id),
                role,
                str(username or "").strip().lstrip("@"),
                str(display_name or "").strip(),
                ROLE_TITLES[role],
                now,
                int(row["created_by"] or OWNER_TG_ID),
            ),
        )
        conn.commit()
        return {"ok": True, "role": role, "title": ROLE_TITLES[role], "user_id": int(user_id)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def grant_role(
    user_id: int,
    role: str,
    *,
    actor_id: int | None = None,
    actor_name: str = "",
    username: str = "",
    display_name: str = "",
) -> dict[str, dict]:
    normalized = normalize_role(role)
    if normalized == "user":
        return load_role_registry()
    if actor_id and int(actor_id) != OWNER_TG_ID:
        raise PermissionError("Only the DeathTG owner can grant roles")
    now = int(time.time())
    with _database() as conn:
        conn.execute(
            """
            INSERT INTO role_grants
                (user_id, role, username, display_name, title, granted_at, granted_by, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_id, role) DO UPDATE SET
                username=excluded.username,
                display_name=excluded.display_name,
                title=excluded.title,
                granted_at=excluded.granted_at,
                granted_by=excluded.granted_by,
                revoked_at=NULL
            """,
            (
                int(user_id),
                normalized,
                str(username or actor_name or "").strip().lstrip("@"),
                str(display_name or "").strip(),
                ROLE_TITLES[normalized],
                now,
                int(actor_id or OWNER_TG_ID),
            ),
        )
    return load_role_registry()


def revoke_role(user_id: int, role: str, *, actor_id: int | None = None) -> dict[str, dict]:
    if actor_id and int(actor_id) != OWNER_TG_ID:
        raise PermissionError("Only the DeathTG owner can revoke roles")
    normalized = normalize_role(role)
    with _database() as conn:
        conn.execute(
            "UPDATE role_grants SET revoked_at=? WHERE user_id=? AND role=? AND revoked_at IS NULL",
            (int(time.time()), int(user_id), normalized),
        )
    return load_role_registry()


def list_role_entries() -> list[dict[str, object]]:
    with _database() as conn:
        rows = conn.execute(
            """
            SELECT user_id, role, username, display_name, title, granted_at, granted_by
            FROM role_grants
            WHERE revoked_at IS NULL
            ORDER BY user_id, role
            """
        ).fetchall()
    grouped: dict[int, dict[str, object]] = {}
    for row in rows:
        user_id = int(row["user_id"])
        item = grouped.setdefault(
            user_id,
            {
                "user_id": user_id,
                "username": str(row["username"] or ""),
                "display_name": str(row["display_name"] or ""),
                "roles": [],
                "titles": [],
                "updated_at": 0,
                "updated_by": str(int(row["granted_by"] or 0)),
            },
        )
        item["roles"].append(str(row["role"]))
        item["titles"].append(str(row["title"] or ROLE_TITLES.get(str(row["role"]), "")))
        item["updated_at"] = max(int(item["updated_at"]), int(row["granted_at"] or 0))
        if not item["username"] and row["username"]:
            item["username"] = str(row["username"])
        if not item["display_name"] and row["display_name"]:
            item["display_name"] = str(row["display_name"])
    return list(grouped.values())


def load_role_registry() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in list_role_entries():
        result[str(item["user_id"])] = {
            "roles": list(item["roles"]),
            "username": str(item["username"]),
            "display_name": str(item["display_name"]),
            "title": ", ".join(str(title) for title in item["titles"]),
            "updated_at": int(item["updated_at"]),
            "updated_by": str(item["updated_by"]),
            "updated_by_name": "",
        }
    return result


def save_role_registry(data: dict[str, dict]) -> dict[str, dict]:
    with _database() as conn:
        conn.execute("DELETE FROM role_grants")
        now = int(time.time())
        for raw_user_id, item in data.items():
            if not str(raw_user_id).isdigit() or not isinstance(item, dict):
                continue
            for role in item.get("roles", []):
                normalized = normalize_role(str(role))
                if normalized == "user":
                    continue
                conn.execute(
                    """
                    INSERT INTO role_grants
                        (user_id, role, username, display_name, title, granted_at, granted_by, revoked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        int(raw_user_id),
                        normalized,
                        str(item.get("username") or "").lstrip("@"),
                        str(item.get("display_name") or ""),
                        str(item.get("title") or ROLE_TITLES[normalized]),
                        int(item.get("updated_at", 0) or now),
                        int(item.get("updated_by", 0) or 0),
                    ),
                )
    return load_role_registry()


def allowed_role(user_id: int, role: str) -> bool:
    normalized = normalize_role(role)
    if normalized == "user" or int(user_id) == OWNER_TG_ID:
        return True
    with _database() as conn:
        row = conn.execute(
            "SELECT 1 FROM role_grants WHERE user_id=? AND role=? AND revoked_at IS NULL",
            (int(user_id), normalized),
        ).fetchone()
    return row is not None


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
