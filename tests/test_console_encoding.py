from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import bootstrap
import dtg


class ConsoleEncodingTests(unittest.TestCase):
    def test_launchers_request_utf8_with_safe_replacement(self) -> None:
        for module in (dtg, bootstrap):
            stdout = Mock()
            stderr = Mock()
            with patch.object(module.sys, "stdout", stdout), patch.object(module.sys, "stderr", stderr):
                module.configure_console_encoding()
            stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
            stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    unittest.main()
