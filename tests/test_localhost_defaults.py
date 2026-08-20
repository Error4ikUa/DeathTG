from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deathtg.panel_access import effective_panel_bind_host
from deathtg.server_bootstrap import ensure_server_env


class LocalhostDefaultTests(unittest.TestCase):
    def test_legacy_wildcard_bind_is_migrated_to_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("PANEL_HOST=0.0.0.0\n", encoding="utf-8")

            env = ensure_server_env(path=env_path)

        self.assertEqual(env["PANEL_HOST"], "127.0.0.1")

    def test_remote_bind_requires_explicit_opt_in(self) -> None:
        with patch.dict(os.environ, {"PANEL_HOST": "0.0.0.0", "PANEL_ALLOW_REMOTE_BIND": "0"}, clear=False):
            self.assertEqual(effective_panel_bind_host(), "127.0.0.1")
        with patch.dict(os.environ, {"PANEL_HOST": "0.0.0.0", "PANEL_ALLOW_REMOTE_BIND": "1"}, clear=False):
            self.assertEqual(effective_panel_bind_host(), "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
