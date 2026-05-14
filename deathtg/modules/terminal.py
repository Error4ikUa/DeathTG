from __future__ import annotations

import asyncio
import shlex

from deathtg.command import command
from deathtg.ui import box, fail

SAFE_COMMANDS = {
    "pwd",
    "ls",
    "dir",
    "whoami",
    "uname",
    "date",
    "uptime",
    "df",
    "free",
    "python",
    "python3",
    "pip",
    "git",
}

BLOCKED_PARTS = {
    "rm",
    "del",
    "format",
    "mkfs",
    "shutdown",
    "reboot",
    "poweroff",
    "dd",
    "curl",
    "wget",
    "nc",
    "netcat",
    "chmod",
    "chown",
    "sudo",
    "su",
}


@command("term", description="Безопасная терминальная команда", usage=".term git status")
async def term_cmd(event, args: list[str]) -> None:
    if not args:
        await event.edit(fail("укажи команду"), parse_mode="html")
        return

    raw = " ".join(args).strip()
    parts = shlex.split(raw)
    if not parts:
        await event.edit(fail("пустая команда"), parse_mode="html")
        return

    base = parts[0]
    if base not in SAFE_COMMANDS or any(part in BLOCKED_PARTS for part in parts):
        await event.edit(fail("команда заблокирована терминальной защитой"), parse_mode="html")
        return

    proc = await asyncio.create_subprocess_exec(
        *parts,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    output = (stdout + stderr).decode("utf-8", errors="replace").strip() or "empty output"
    output = output[-3500:]
    await event.edit(box("Terminal", [f"<code>{raw}</code>", f"<pre>{output}</pre>"]), parse_mode="html")
