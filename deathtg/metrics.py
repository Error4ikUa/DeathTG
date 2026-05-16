from __future__ import annotations

"""
Asynchronous usage statistics for DeathTG.

This module implements a simple metrics system backed by SQLite via
``aiosqlite``.  It records when commands are executed, counts usage
over time and determines how many days the bot has been installed.
All functions are asynchronous and must be awaited to obtain their
results.

Database schema:

* ``meta``: holds a single row with the ``installed_at`` timestamp.
* ``command_usage``: logs each command invocation with module,
  command name and timestamp.
"""

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from deathtg.config import ROOT_DIR


DB_PATH = ROOT_DIR / "deathtg_stats.sqlite3"


@dataclass(slots=True)
class UsagePoint:
    """Represents a usage aggregate for a given day and module."""

    day: str
    count: int


@asynccontextmanager
async def get_db():
    """Yield an aiosqlite connection with row factory set to return dicts."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def init_metrics() -> None:
    """Initialise the metrics database if it has not been created yet."""
    async with get_db() as conn:
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
            await conn.execute(
                "INSERT INTO meta(key, value) VALUES('installed_at', ?)",
                (str(int(time.time())),),
            )
        await conn.commit()


async def record_command(module: str, command: str) -> None:
    """Record that a command has been executed."""
    await init_metrics()
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO command_usage(module, command, used_at) VALUES(?, ?, ?)",
            (module, command, int(time.time())),
        )
        await conn.commit()


async def usage_total() -> int:
    """Return the total number of recorded command invocations."""
    await init_metrics()
    async with get_db() as conn:
        async with conn.execute("SELECT COUNT(*) AS c FROM command_usage") as cursor:
            row = await cursor.fetchone()
            return int(row["c"]) if row else 0


async def installed_days() -> int:
    """Return the number of days since installation (minimum 1)."""
    await init_metrics()
    async with get_db() as conn:
        async with conn.execute("SELECT value FROM meta WHERE key='installed_at'") as cursor:
            row = await cursor.fetchone()
            installed_at = int(row["value"]) if row else int(time.time())
    return max(1, int((time.time() - installed_at) // 86400) + 1)


async def usage_by_day(days: int = 14) -> list[dict[str, object]]:
    """Return aggregated usage counts by day, module and command."""
    await init_metrics()
    since = int(time.time()) - days * 86400
    async with get_db() as conn:
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
    """Return the most used modules, sorted by usage count."""
    await init_metrics()
    async with get_db() as conn:
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
    """Return account level based on account age inside DeathTG.

    Usage counters are activity stats, not account strength.  Leveling
    uses installed days so it cannot be farmed by spamming commands.
    """
    days = await installed_days()
    level = days // 7 + 1
    current = days % 7
    next_needed = 7 - current if current else 7
    elo = 700 + days * 12
    return {"level": level, "current": current, "next_needed": next_needed, "elo": elo}
