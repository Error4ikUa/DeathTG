from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from telethon import events

CommandHandler = Callable[[events.NewMessage.Event, list[str]], Awaitable[Any]]


@dataclass(slots=True)
class Command:
    name: str
    handler: CommandHandler
    description: str = "No description"
    usage: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    module: str = "unknown"
    security: str | int | None = None


def command(
    name: str,
    *,
    description: str = "No description",
    usage: str = "",
    aliases: tuple[str, ...] | list[str] = (),
    security: str | int | None = None,
    permissions: str | int | None = None,
):
    effective_security = security if security is not None else permissions

    def decorator(func: CommandHandler) -> CommandHandler:
        setattr(
            func,
            "__deathtg_command__",
            {
                "name": name.lower().strip(),
                "description": description,
                "usage": usage,
                "aliases": tuple(alias.lower().strip() for alias in aliases),
                "security": effective_security,
            },
        )
        return func

    return decorator
