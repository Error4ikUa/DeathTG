from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from deathtg.config import ROOT_DIR

DB_PATH = ROOT_DIR / "deathtg_stats.sqlite3"


@dataclass(slots=True)
class UsagePoint:
    day: str
    count: int


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_metrics() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS command_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                command TEXT NOT NULL,
                used_at INTEGER NOT NULL
            )
            """
        )
        exists = conn.execute("SELECT value FROM meta WHERE key='installed_at'").fetchone()
        if not exists:
            conn.execute("INSERT INTO meta(key, value) VALUES('installed_at', ?)", (str(int(time.time())),))


def record_command(module: str, command: str) -> None:
    init_metrics()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO command_usage(module, command, used_at) VALUES(?, ?, ?)",
            (module, command, int(time.time())),
        )


def usage_total() -> int:
    init_metrics()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM command_usage").fetchone()
        return int(row["c"])


def installed_days() -> int:
    init_metrics()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='installed_at'").fetchone()
        installed_at = int(row["value"]) if row else int(time.time())
    return max(1, int((time.time() - installed_at) // 86400) + 1)


def usage_by_day(days: int = 14) -> list[dict[str, object]]:
    init_metrics()
    since = int(time.time()) - days * 86400
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT date(used_at, 'unixepoch') AS day, module, command, COUNT(*) AS count
            FROM command_usage
            WHERE used_at >= ?
            GROUP BY day, module, command
            ORDER BY day ASC, count DESC
            """,
            (since,),
        ).fetchall()
    return [dict(row) for row in rows]


def top_modules(limit: int = 7) -> list[dict[str, object]]:
    init_metrics()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT module, COUNT(*) AS count
            FROM command_usage
            GROUP BY module
            ORDER BY count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def level_info() -> dict[str, int]:
    total = usage_total()
    level = total // 100 + 1
    current = total % 100
    next_needed = 100 - current
    elo = 700 + total * 3
    return {"level": level, "current": current, "next_needed": next_needed, "elo": elo}
