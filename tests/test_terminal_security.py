from __future__ import annotations

import unittest

from deathtg.modules.terminal import TerminalMod


class TerminalSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module = TerminalMod()

    async def test_shell_operators_are_never_executed(self) -> None:
        for command in ("ls & type .env", "ls; cat .env", "pwd | tee leak", "ls $(whoami)"):
            allowed, _reason = self.module._is_safe(command)
            self.assertFalse(allowed, command)

    async def test_python_code_execution_is_blocked_but_version_is_available(self) -> None:
        self.assertFalse(self.module._is_safe("python -c 'print(1)'")[0])
        self.assertTrue(self.module._is_safe("python --version")[0])
        self.assertIn("3.", await self.module._run("python --version"))


if __name__ == "__main__":
    unittest.main()
