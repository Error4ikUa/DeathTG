from __future__ import annotations

from deathtg.command import command
from deathtg.ui import ok


@command("test", description="Тестовый внешний модуль", usage=".test")
async def test_cmd(event, args: list[str]) -> None:
    await event.edit(ok("внешний модуль работает"), parse_mode="html")
