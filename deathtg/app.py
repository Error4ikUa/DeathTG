from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telethon import TelegramClient, events

from deathtg.config import DeathTGConfig, MODULES_DIR
from deathtg.loader import ModuleLoader
from deathtg.metrics import init_metrics, record_command
from deathtg.registry import CommandRegistry
from deathtg.ui import CONSOLE_BANNER, fail

log = logging.getLogger("deathtg")


class DeathTG:
    def __init__(self, config: DeathTGConfig) -> None:
        self.config = config
        self.client = TelegramClient(config.session_name, config.api_id, config.api_hash)
        self.client.deathtg_app = self
        self.registry = CommandRegistry()
        self.loader = ModuleLoader(self.registry, MODULES_DIR)

    async def start(self) -> None:
        print(CONSOLE_BANNER)
        init_metrics()
        await self.client.start()

        me = await self.client.get_me()
        if self.config.owner_id is None:
            self.config.owner_id = me.id

        await self.loader.load_builtin("deathtg.modules", ["core", "system", "antivirus", "terminal"])
        await self.loader.load_all_local()

        self.client.add_event_handler(self._dispatch, events.NewMessage(outgoing=True))
        log.info("DeathTG started as @%s", getattr(me, "username", None) or me.id)
        await self.client.run_until_disconnected()

    async def _dispatch(self, event: events.NewMessage.Event) -> None:
        text = event.raw_text or ""
        prefix = self.config.command_prefix
        if not text.startswith(prefix):
            return

        raw = text[len(prefix):].strip()
        if not raw:
            return

        command_name, *args = raw.split()
        command = self.registry.get(command_name)
        if command is None:
            return

        try:
            record_command(command.module, command.name)
            await command.handler(event, args)
        except Exception as exc:
            log.exception("Command failed: %s", command.name)
            await event.edit(fail(f"ошибка в .{command.name}: <code>{type(exc).__name__}: {exc}</code>"), parse_mode="html")

    def module_file(self, name: str) -> Path:
        safe_name = Path(name).name
        if not safe_name.endswith(".py"):
            safe_name += ".py"
        return MODULES_DIR / safe_name


def run_async(config: DeathTGConfig) -> None:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
    bot = DeathTG(config)
    asyncio.run(bot.start())
