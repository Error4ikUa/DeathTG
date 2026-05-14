from __future__ import annotations

from pathlib import Path

from deathtg.command import command
from deathtg.config import MODULES_DIR
from deathtg.ui import box, fail, ok


def _app(event):
    return event.client._event_builders[0][0].callback.__self__


@command("help", description="Показать все модули и команды", usage=".help [module]", aliases=("h",))
async def help_cmd(event, args: list[str]) -> None:
    app = _app(event)
    grouped = app.registry.by_module()

    if args:
        module_name = args[0]
        commands = grouped.get(module_name)
        if not commands:
            await event.edit(fail(f"модуль <code>{module_name}</code> не найден"), parse_mode="html")
            return
        lines = [f"<code>.{cmd.name}</code> — {cmd.description}" for cmd in commands]
        await event.edit(box(f"Модуль {module_name}", lines), parse_mode="html")
        return

    lines: list[str] = []
    for module, commands in sorted(grouped.items()):
        names = ", ".join(f".{cmd.name}" for cmd in sorted(commands, key=lambda item: item.name))
        lines.append(f"<b>{module}</b>: {names}")

    await event.edit(box("DeathTG help", lines), parse_mode="html")


@command("dlmod", description="Скачать и загрузить модуль по ссылке", usage=".dlmod https://...")
async def dlmod_cmd(event, args: list[str]) -> None:
    app = _app(event)
    if not args:
        await event.edit(fail("дай ссылку на .py модуль"), parse_mode="html")
        return

    msg = await event.edit("<b>☠️ DeathTG:</b> качаю модуль...", parse_mode="html")
    path = await app.loader.download_module(args[0])
    module_name = await app.loader.load_file(path)
    await msg.edit(ok(f"модуль <code>{module_name}</code> скачан и загружен"), parse_mode="html")


@command("loadmod", description="Загрузить локальный модуль из папки modules", usage=".loadmod filename.py")
async def loadmod_cmd(event, args: list[str]) -> None:
    app = _app(event)
    if not args:
        await event.edit(fail("укажи файл: <code>.loadmod example.py</code>"), parse_mode="html")
        return

    path = MODULES_DIR / Path(args[0]).name
    module_name = await app.loader.load_file(path)
    await event.edit(ok(f"модуль <code>{module_name}</code> загружен"), parse_mode="html")


@command("unloadmod", description="Выгрузить модуль", usage=".unloadmod module_name")
async def unloadmod_cmd(event, args: list[str]) -> None:
    app = _app(event)
    if not args:
        await event.edit(fail("укажи имя модуля"), parse_mode="html")
        return

    module_name = args[0]
    removed = app.loader.unload(module_name)
    await event.edit(ok(f"модуль <code>{module_name}</code> выгружен, команд снято: {len(removed)}"), parse_mode="html")


@command("modules", description="Список загруженных модулей", usage=".modules")
async def modules_cmd(event, args: list[str]) -> None:
    app = _app(event)
    lines = [f"<code>{name}</code>" for name in sorted(app.loader.loaded)]
    await event.edit(box("Загруженные модули", lines or ["пока пусто"]), parse_mode="html")
