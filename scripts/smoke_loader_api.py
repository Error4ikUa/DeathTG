from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deathtg.config import MODULES_DIR
from deathtg.loader import ConfigValue, Module, ModuleConfig, ModuleLoader, validators, watcher
from deathtg.module_db import ModuleDatabase
from deathtg.registry import CommandRegistry


class DummyModule(Module):
    config = ModuleConfig(
        ConfigValue("enabled", True, "Enable dummy", validators.Boolean()),
        ConfigValue("limit", 3, "Limit", validators.Integer(minimum=1, maximum=10)),
    )

    @watcher("out", "no_commands")
    async def watcher_example(self, event):
        return None


async def main() -> None:
    registry = CommandRegistry()
    loader = ModuleLoader(registry, MODULES_DIR)
    await loader.load_builtin("deathtg.modules", ["core", "root", "system", "terminal", "antivirus"])
    commands = {command.name for command in registry.all()}
    assert "help" in commands
    assert "helpb" in commands
    assert "root" in commands

    with tempfile.TemporaryDirectory() as tmp:
        db = ModuleDatabase(Path(tmp) / "module_db.json")
        dummy = DummyModule()
        dummy._module_name = "dummy"
        dummy._db = db
        dummy.set("answer", 42)
        assert dummy.get("answer") == 42
        dummy.config["enabled"] = "yes"
        dummy.config["limit"] = "5"
        assert dummy.config["enabled"] is True
        assert dummy.config["limit"] == 5


if __name__ == "__main__":
    asyncio.run(main())
