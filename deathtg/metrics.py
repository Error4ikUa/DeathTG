from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from deathtg.config import ROOT_DIR

DB_PATH = ROOT_DIR / "deathtg_stats.sqlite3"

@dataclass(slots=True)
class UsagePoint:
    day: str
    count: int

async def _connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn

async def init_metrics() -> None:
    async with await _connect() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS command_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                command TEXT NOT NULL,
                used_at INTEGER NOT NULL
            )
            """
        )
        async with conn.execute("SELECT value FROM meta WHERE key='installed_at'") as cursor:
            exists = await cursor.fetchone()
        
        if not exists:
            await conn.execute("INSERT INTO meta(key, value) VALUES('installed_at', ?)", (str(int(time.time())),))
        
        await conn.commit()

async def record_command(module: str, command: str) -> None:
    await init_metrics()
    async with await _connect() as conn:
        await conn.execute(
            "INSERT INTO command_usage(module, command, used_at) VALUES(?, ?, ?)",
            (module, command, int(time.time())),
        )
        await conn.commit()

async def usage_total() -> int:
    await init_metrics()
    async with await _connect() as conn:
        async with conn.execute("SELECT COUNT(*) AS c FROM command_usage") as cursor:
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0

async def installed_days() -> int:
    await init_metrics()
    async with await _connect() as conn:
        async with conn.execute("SELECT value FROM meta WHERE key='installed_at'") as cursor:
            row = await cursor.fetchone()
            installed_at = int(row["value"]) if row else int(time.time())
    return max(1, int((time.time() - installed_at) // 86400) + 1)

async def usage_by_day(days: int = 14) -> list[dict[str, object]]:
    await init_metrics()
    since = int(time.time()) - days * 86400
    async with await _connect() as conn:
        async with conn.execute(
            """
            SELECT date(used_at, 'unixepoch') AS day, module, command, COUNT(*) AS count
            FROM command_usage
            WHERE used_at >= ?
            GROUP BY day, module, command
            ORDER BY day ASC, count DESC
            """,
            (since,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]

async def top_modules(limit: int = 7) -> list[dict[str, object]]:
    await init_metrics()
    async with await _connect() as conn:
        async with conn.execute(
            """
            SELECT module, COUNT(*) AS count
            FROM command_usage
            GROUP BY module
            ORDER BY count DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [dict(row) for row in rows]

async def level_info() -> dict[str, int]:
    total = await usage_total()
    level = total // 100 + 1
    current = total % 100
    next_needed = 100 - current
    elo = 700 + total * 3
    return {"level": level, "current": current, "next_needed": next_needed, "elo": elo}
