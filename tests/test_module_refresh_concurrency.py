from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from deathtg.panel import clean_core


class _FakeRegistry:
    def __init__(self) -> None:
        self._commands = {"old": object()}
        self._aliases = {"old": "old"}


class _FakeLoader:
    def __init__(self) -> None:
        self.loaded = {"old": object()}
        self.import_names = {"old": "old"}
        self.source_paths = {"old": "old"}
        self.instances = {"old": object()}
        self.watchers = [object()]
        self.raw_handlers = [object()]
        self.inline_handlers = {"old": object()}
        self.callback_handlers = {"old": object()}
        self.active = 0
        self.max_active = 0
        self.completed = 0

    async def load_builtin(self, *_args, **_kwargs) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1

    async def load_all_local(self, **_kwargs) -> None:
        await asyncio.sleep(0.01)
        self.completed += 1


class ModuleRefreshConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_refreshes_never_clear_the_registry_at_the_same_time(self) -> None:
        fake_registry = _FakeRegistry()
        fake_loader = _FakeLoader()
        fresh_lock = asyncio.Lock()

        with (
            patch.object(clean_core, "registry", fake_registry),
            patch.object(clean_core, "loader", fake_loader),
            patch.object(clean_core, "MODULE_REFRESH_LOCK", fresh_lock),
            patch.object(clean_core, "load_module_meta", return_value={}),
        ):
            await asyncio.gather(clean_core.refresh_modules(), clean_core.refresh_modules())

        self.assertEqual(fake_loader.max_active, 1)
        self.assertEqual(fake_loader.completed, 2)


if __name__ == "__main__":
    unittest.main()
