from __future__ import annotations

from pathlib import Path

from deathtg.command import command
from deathtg.config import MODULES_DIR
from deathtg.security import scan_module_source
from deathtg.ui import box, fail, ok


@command("scanmod", description="Проверить модуль защитой DeathTG", usage=".scanmod module.py")
async def scanmod_cmd(event, args: list[str]) -> None:
    if not args:
        await event.edit(fail("укажи файл модуля: <code>.scanmod test.py</code>"), parse_mode="html")
        return

    path = MODULES_DIR / Path(args[0]).name
    if not path.exists():
        await event.edit(fail(f"файл <code>{path.name}</code> не найден"), parse_mode="html")
        return

    report = scan_module_source(path.read_text(encoding="utf-8"))
    verdict = "разрешён" if report.allowed else "заблокирован"
    await event.edit(
        box(
            "🛡 DeathTG Antivirus",
            [
                f"Файл: <code>{path.name}</code>",
                f"Вердикт: <b>{verdict}</b>",
                f"Score: <code>{report.score}</code>",
                "Причины:",
                report.pretty(),
            ],
        ),
        parse_mode="html",
    )


@command("antivirus", description="Статус защиты модулей", usage=".antivirus")
async def antivirus_cmd(event, args: list[str]) -> None:
    await event.edit(
        box(
            "🛡 DeathTG Antivirus",
            [
                "Статус: <b>включён</b>",
                "Проверяет: delete account, logout, session, eval/exec, shell, file wipe",
                "Защищённые модули: <code>core</code>, <code>system</code>, <code>antivirus</code>, <code>terminal</code>",
            ],
        ),
        parse_mode="html",
    )
