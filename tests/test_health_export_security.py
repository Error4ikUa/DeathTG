from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from deathtg import health_tools


class HealthExportSecurityTests(unittest.TestCase):
    def test_export_redacts_tokens_and_private_panel_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            token = "1234567890:" + "A" * 35
            private_link = "http://127.0.0.1:8080/site/site-id/u2054091032/private-token"
            (runtime / "deathtg.log").write_text(
                f"BOT_TOKEN={token}\nsetup_token=secret-value\npanel={private_link}\n",
                encoding="utf-8",
            )
            exports = runtime / "health_exports"
            with (
                patch.object(health_tools, "RUNTIME_DIR", runtime),
                patch.object(health_tools, "HEALTH_EXPORTS_DIR", exports),
                patch.object(health_tools, "HEALTH_STATE_PATH", runtime / "health_state.json"),
            ):
                output = health_tools.export_logs_bundle()

            with zipfile.ZipFile(output) as archive:
                content = archive.read("deathtg.log").decode("utf-8")
            self.assertNotIn(token, content)
            self.assertNotIn("secret-value", content)
            self.assertNotIn("private-token", content)
            self.assertIn("REDACTED", content)


if __name__ == "__main__":
    unittest.main()
