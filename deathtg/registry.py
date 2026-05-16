from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from deathtg.command import Command

PROTECTED_MODULES = {"core", "root", "info", "system", "antivirus", "terminal"}


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, str] = {}

    def add(self, command: Command) -> None:
        if command.name in self._commands:
            raise ValueError(f"Command already exists: {command.name}")

        self._commands[command.name] = command
        for alias in command.aliases:
            if alias in self._aliases or alias in self._commands:
                raise ValueError(f"Alias is already used: {alias}")
            self._aliases[alias] = command.name

    def remove_module(self, module_name: str, *, force: bool = False) -> list[str]:
        if module_name in PROTECTED_MODULES and not force:
            raise RuntimeError(f"Protected module cannot be removed: {module_name}")

        removed: list[str] = []
        for name, cmd in list(self._commands.items()):
            if cmd.module == module_name:
                removed.append(name)
                del self._commands[name]

        for alias, target in list(self._aliases.items()):
            if target in removed:
                del self._aliases[alias]

        return removed

    def get(self, name: str) -> Command | None:
        normalized = name.lower().strip()
        real_name = self._aliases.get(normalized, normalized)
        return self._commands.get(real_name)

    def all(self) -> Iterable[Command]:
        return self._commands.values()

    def by_module(self) -> dict[str, list[Command]]:
        grouped: dict[str, list[Command]] = defaultdict(list)
        for cmd in self._commands.values():
            grouped[cmd.module].append(cmd)
        return dict(grouped)
