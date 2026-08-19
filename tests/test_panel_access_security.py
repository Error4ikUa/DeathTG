from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deathtg.panel_access as panel_access


class PanelAccessSecurityTests(unittest.TestCase):
    def test_device_grant_is_atomic_and_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with (
                patch.object(panel_access, "GRANTS_PATH", runtime / "grants.json"),
                patch.object(panel_access, "DEVICES_PATH", runtime / "devices.json"),
                patch.dict(panel_access.os.environ, {"PANEL_SECRET": "test-secret-with-enough-entropy"}),
            ):
                url = panel_access.issue_device_grant("Test phone")
                token = url.rsplit("/", 1)[-1]

                def consume() -> bool:
                    try:
                        panel_access.consume_device_grant(token, ip="100.64.0.10", user_agent="test")
                        return True
                    except RuntimeError:
                        return False

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: consume(), range(2)))

                self.assertEqual(results.count(True), 1)
                self.assertEqual(results.count(False), 1)
                self.assertEqual(len(panel_access.list_devices()), 1)

    def test_revoked_device_cannot_become_active_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with patch.object(panel_access, "DEVICES_PATH", runtime / "devices.json"):
                panel_access.remember_device_session("session", label="Phone")
                panel_access.revoke_device_session("session")

                self.assertIsNone(panel_access.active_device("session"))

    def test_grants_fail_closed_without_panel_secret(self) -> None:
        with patch.dict(panel_access.os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "PANEL_SECRET"):
                panel_access.issue_device_grant("Phone")


if __name__ == "__main__":
    unittest.main()
