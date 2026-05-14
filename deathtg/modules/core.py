from __future__ import annotations

import subprocess
from pathlib import Path

from deathtg.command import command
from deathtg.config import MODULES_DIR, ROOT_DIR
from deathtg.registry import PROTECTED_MODULES
from deathtg.ui import box, fail, ok


def _app(event):
    return event.client.deathtg_app


def _copy_command(name: str) -> str:
    return f"<code>.{name}</code>"


@command("help", description="Красивый список модулей и команд DeathTG", usage=".help [module]", aliases=("h",))
async def help_cmd(event, args: list[str]) -> None:
    app = _app(event)
    grouped = app.registry.by_module()
    me = await event.client.get_me()
    nickname = me.first_name or me.username or "DeathTG user"

    if args:
        module_name = args[0]
        commands = grouped.get(module_name)
        if not commands:
            await event.edit(fail(f"модуль <code>{module_name}</code> не найден"), parse_mode="html")
            return
        icon = "🛡" if module_name in PROTECTED_MODULES else "🧩"
        lines = [
            f"Приветствую, <b>{nickname}</b>",
            f"{icon} Модуль: <code>{module_name}</code>",
            "",
            "📗 <b>Команды:</b>",
        ]
        for cmd in sorted(commands, key=lambda item: item.name):
            usage = f" — <i>{cmd.usage}</i>" if cmd.usage else ""
            lines.append(f"  {_copy_command(cmd.name)} — {cmd.description}{usage}")
        await event.edit(box(f"DeathTG / {module_name}", lines), parse_mode="html")
        return

    lines: list[str] = [
        f"Приветствую, <b>{nickname}</b>",
        "📗 <b>Установленные модули DeathTG</b>",
        "",
    ]
    for module, commands in sorted(grouped.items()):
        icon = "🛡" if module in PROTECTED_MODULES else "🧩"
        names = " ".join(_copy_command(cmd.name) for cmd in sorted(commands, key=lambda item: item.name))
        lock = " <i>protected</i>" if module in PROTECTED_MODULES else ""
        lines.append(f"{icon} <b>{module}</b>{lock}\n{names}")

    lines.append("\n💚 <i>.help module_name — открыть описание модуля</i>")
    await event.edit(box("DeathTG Help", lines), parse_mode="html")


@command("tdgup", description="Обновить DeathTG до последней версии", usage=".tdgup", aliases=("update", "dtgup"))
async def tdgup_cmd(event, args: list[str]) -> None:
    msg = await event.edit("<b>☠️ DeathTG:</b> проверяю обновления...", parse_mode="html")
    try:
        result = subprocess.run(["git", "pull"], cwd=ROOT_DIR, text=True, capture_output=True, timeout=90)
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode != 0:
            await msg.edit(fail("обновление не выполнено:\n<code>" + output[-1200:] + "</code>"), parse_mode="html")
            return
        short = output[-1200:] or "Already up to date."
        await msg.edit(ok("DeathTG обновлён. Перезапусти процесс, чтобы сайт и юзербот взяли новый код.\n<code>" + short + "</code>"), parse_mode="html")
    except Exception as exc:
        await msg.edit(fail(f"ошибка обновления: <code>{type(exc).__name__}: {exc}</code>"), parse_mode="html")


@command("dlmod", description="Скачать и загрузить модуль по ссылке", usage=".dlmod https://...")
async def dlmod_cmd(event, args: list[str]) -> None:
    app = _app(event)
    if not args:
        await event.edit(fail("дай ссылку на .py модуль"), parse_mode="html")
        return

    msg = await event.edit("<b>☠️ DeathTG:</b> качаю модуль и проверяю защитой...", parse_mode="html")
    try:
        path = await app.loader.download_module(args[0])
        try:
            module_name = await app.loader.load_file(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        await msg.edit(ok(f"модуль <code>{module_name}</code> скачан, проверен и загружен"), parse_mode="html")
    except Exception as exc:
        await msg.edit(fail(f"ошибка в .dlmod: <code>{type(exc).__name__}: {exc}</code>"), parse_mode="html")


@command("loadmod", description="Загрузить локальный модуль из папки modules", usage=".loadmod filename.py")
async def loadmod_cmd(event, args: list[str]) -> None:
    app = _app(event)
    if not args:
        await event.edit(fail("укажи файл: <code>.loadmod example.py</code>"), parse_mode="html")
        return

    path = MODULES_DIR / Path(args[0]).name
    module_name = await app.loader.load_file(path)
    await event.edit(ok(f"модуль <code>{module_name}</code> загружен"), parse_mode="html")


@command("unloadmod", description="Выгрузить модуль", usage=".unloadmod module_name", aliases=("unloadnod",))
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
    lines = []
    for name in sorted(app.loader.loaded):
        icon = "🛡" if name in PROTECTED_MODULES else "🧩"
        lines.append(f"{icon} <code>{name}</code>")
    await event.edit(box("Загруженные модули", lines or ["пока пусто"]), parse_mode="html")
