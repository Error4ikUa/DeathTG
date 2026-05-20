from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deathtg.config import RUNTIME_DIR

STATE_DB = RUNTIME_DIR / "state.db"
SCHEMA_VERSION = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class StateChange:
    table_name: str
    record_id: str
    action: str
    changed_fields: list[str]


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bots (
        bot_key TEXT PRIMARY KEY,
        username TEXT,
        bot_id TEXT,
        token_present INTEGER DEFAULT 0,
        status TEXT,
        error TEXT,
        last_checked TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS telegram_resources (
        resource_key TEXT PRIMARY KEY,
        resource_type TEXT NOT NULL,
        title TEXT,
        username TEXT,
        resource_id TEXT,
        status TEXT,
        error TEXT,
        metadata_json TEXT,
        last_checked TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS modules (
        module_key TEXT PRIMARY KEY,
        name TEXT,
        status TEXT,
        enabled INTEGER DEFAULT 1,
        source_url TEXT,
        version TEXT,
        author TEXT,
        has_requirements INTEGER DEFAULT 0,
        antivirus_status TEXT,
        error TEXT,
        last_loaded TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS module_sources (
        source_id TEXT PRIMARY KEY,
        module_key TEXT,
        source_type TEXT,
        url TEXT,
        path TEXT,
        trusted INTEGER DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS module_requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module_key TEXT NOT NULL,
        requirement TEXT NOT NULL,
        installed INTEGER DEFAULT 0,
        error TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(module_key, requirement)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS health_checks (
        check_key TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        message TEXT,
        details_json TEXT,
        checked_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL,
        entity_type TEXT,
        entity_id TEXT,
        details_json TEXT,
        created_at TEXT NOT NULL
    )
    """,
)


def connect(path: Path = STATE_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_state_db(path: Path = STATE_DB) -> None:
    with connect(path) as conn:
        for statement in SCHEMA:
            conn.execute(statement)
        conn.execute(
            "INSERT OR IGNORE INTO migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, now_iso()),
        )
        conn.commit()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def event(
    event_type: str,
    message: str,
    *,
    level: str = "info",
    entity_type: str = "",
    entity_id: str = "",
    details: Any = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    own = conn is None
    if own:
        conn = connect()
    assert conn is not None
    conn.execute(
        """
        INSERT INTO events(event_type, level, message, entity_type, entity_id, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (event_type, level, message, entity_type, entity_id, _json(details or {}), now_iso()),
    )
    if own:
        conn.commit()
        conn.close()


def _table_columns(table: str) -> set[str]:
    ensure_state_db()
    with connect() as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def upsert(
    table: str,
    key_column: str,
    key_value: str,
    data: dict[str, Any],
    *,
    preserve_existing: bool = True,
    event_type: str | None = None,
) -> StateChange:
    ensure_state_db()
    clean = {k: v for k, v in data.items() if k != key_column}
    ts = now_iso()
    if "updated_at" in _table_columns(table):
        clean["updated_at"] = ts

    with connect() as conn:
        existing = conn.execute(f"SELECT * FROM {table} WHERE {key_column}=?", (key_value,)).fetchone()
        if existing is None:
            columns = [key_column, *clean.keys()]
            values = [key_value, *clean.values()]
            placeholders = ",".join("?" for _ in columns)
            conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})", values)
            change = StateChange(table, key_value, "insert", list(clean.keys()))
            event(event_type or f"{table}.insert", f"Inserted {table}:{key_value}", entity_type=table, entity_id=key_value, details=asdict(change), conn=conn)
            conn.commit()
            return change

        current = dict(existing)
        updates: dict[str, Any] = {}
        changed: list[str] = []
        for field, value in clean.items():
            if preserve_existing and value in (None, "") and current.get(field) not in (None, ""):
                continue
            if str(current.get(field) if current.get(field) is not None else "") != str(value if value is not None else ""):
                updates[field] = value
                changed.append(field)

        if updates:
            assignments = ", ".join(f"{field}=?" for field in updates)
            conn.execute(f"UPDATE {table} SET {assignments} WHERE {key_column}=?", [*updates.values(), key_value])
            change = StateChange(table, key_value, "update", changed)
            event(event_type or f"{table}.update", f"Updated {table}:{key_value}", entity_type=table, entity_id=key_value, details=asdict(change), conn=conn)
        else:
            change = StateChange(table, key_value, "noop", [])
        conn.commit()
        return change


def set_setting(key: str, value: Any) -> StateChange:
    return upsert("settings", "key", key, {"value": _json(value) if isinstance(value, (dict, list)) else str(value)})


def set_health(check_key: str, status: str, message: str = "", details: Any = None) -> StateChange:
    return upsert(
        "health_checks",
        "check_key",
        check_key,
        {"status": status, "message": message, "details_json": _json(details or {}), "checked_at": now_iso()},
        preserve_existing=False,
        event_type="health.update",
    )


def set_resource(
    resource_key: str,
    resource_type: str,
    *,
    title: str = "",
    username: str = "",
    resource_id: str = "",
    status: str = "unknown",
    error: str = "",
    metadata: Any = None,
) -> StateChange:
    return upsert(
        "telegram_resources",
        "resource_key",
        resource_key,
        {
            "resource_type": resource_type,
            "title": title,
            "username": username.lstrip("@") if username else "",
            "resource_id": str(resource_id or ""),
            "status": status,
            "error": error,
            "metadata_json": _json(metadata or {}),
            "last_checked": now_iso(),
        },
        preserve_existing=True,
        event_type="resource.update",
    )


def sync_settings_from_config(config: dict[str, Any]) -> None:
    ensure_state_db()
    for key, value in config.items():
        if key in {"api_hash", "bot_token", "bot_token_helper", "panel_password", "panel_secret", "phone"}:
            set_setting(key, "***" if value else "")
        else:
            set_setting(key, value)


def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    ensure_state_db()
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
