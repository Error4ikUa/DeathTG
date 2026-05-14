from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Any

from telethon import events

CommandHandler = Callable[[events.NewMessage.Event, list[str]], Awaitable[Any]]


@dataclass(slots=True)
class Command:
    name: str
    handler: CommandHandler
    description: str = "Без описания"
    usage: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    module: str = "unknown"


def command(
    name: str,
    *,
    description: str = "Без описания",
    usage: str = "",
    aliases: tuple[str, ...] | list[str] = (),
):
    def decorator(func: CommandHandler) -> CommandHandler:
        setattr(
            func,
            "__deathtg_command__",
            {
                "name": name.lower().strip(),
                "description": description,
                "usage": usage,
                "aliases": tuple(alias.lower().strip() for alias in aliases),
            },
        )
        return func

    return decorator
