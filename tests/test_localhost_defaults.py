from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deathtg.panel_access import effective_panel_bind_host
from deathtg.server_bootstrap import ensure_server_env, main as server_bootstrap_main, render_systemd_service


class LocalhostDefaultTests(unittest.TestCase):
    def test_systemd_install_marks_deathtg_as_a_server(self) -> None:
        service = render_systemd_service(Path("/opt/DeathTG"), Path("/opt/DeathTG/.venv/bin/python"))

        self.assertIn("Environment=DEATHTG_SERVER_INSTALL=1", service)

    def test_legacy_wildcard_bind_is_migrated_to_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("PANEL_HOST=0.0.0.0\nPANEL_ALLOW_REMOTE_BIND=1\n", encoding="utf-8")

            env = ensure_server_env(path=env_path)

        self.assertEqual(env["PANEL_HOST"], "127.0.0.1")
        self.assertEqual(env["PANEL_ALLOW_REMOTE_BIND"], "0")
        self.assertEqual(env["PANEL_TAILSCALE_DIRECT"], "1")
        self.assertEqual(env["PANEL_TAILSCALE_AUTO_SERVE"], "0")

    def test_remote_bind_setting_cannot_expose_the_panel_listener(self) -> None:
        with patch.dict(os.environ, {"PANEL_HOST": "0.0.0.0", "PANEL_ALLOW_REMOTE_BIND": "0"}, clear=False):
            self.assertEqual(effective_panel_bind_host(), "127.0.0.1")
        with patch.dict(os.environ, {"PANEL_HOST": "0.0.0.0", "PANEL_ALLOW_REMOTE_BIND": "1"}, clear=False):
            self.assertEqual(effective_panel_bind_host(), "127.0.0.1")

    def test_server_bootstrap_rejects_a_non_loopback_bind_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            with self.assertRaisesRegex(ValueError, "must bind to localhost"):
                ensure_server_env(path=env_path, panel_host="0.0.0.0")

    def test_server_bootstrap_rejects_public_panel_urls(self) -> None:
        with patch("sys.argv", ["server_bootstrap", "--public-url", "https://panel.example.com"]):
            with self.assertRaises(SystemExit) as raised:
                server_bootstrap_main()
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
