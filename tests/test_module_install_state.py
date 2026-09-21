from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deathtg.panel.clean_core as clean_core
from deathtg.security import scan_module_source


class ModuleInstallStateTests(unittest.TestCase):
    def test_catalog_merges_loaded_and_downloaded_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modules_dir = Path(tmp)
            (modules_dir / "NoteDtg.py").write_text("VALUE = 1\n", encoding="utf-8")
            repo_items = [
                {"name": "DownloaderDtg"},
                {"name": "NoteDtg"},
                {"name": "AdminToolsDtg"},
            ]
            with (
                patch.object(clean_core, "MODULES_DIR", modules_dir),
                patch.object(clean_core, "load_module_meta", return_value={}),
            ):
                annotated = clean_core.annotate_repo_install_state(
                    repo_items,
                    {"DownloaderDtg": []},
                )

        by_name = {item["name"]: item for item in annotated}
        self.assertTrue(by_name["DownloaderDtg"]["loaded"])
        self.assertTrue(by_name["DownloaderDtg"]["installed"])
        self.assertTrue(by_name["NoteDtg"]["downloaded"])
        self.assertFalse(by_name["NoteDtg"]["installed"])
        self.assertFalse(by_name["AdminToolsDtg"]["installed"])

    def test_verified_source_cannot_hide_critical_account_action(self) -> None:
        report = scan_module_source(
            "from telethon.tl.functions.account import DeleteAccountRequest\n"
            "request = DeleteAccountRequest(reason='x')\n",
            trusted=True,
        )

        self.assertFalse(report.allowed)
        self.assertEqual(report.verdict, "BLOCKED")

    def test_verified_session_guard_capability_remains_reviewable_but_loadable(self) -> None:
        report = scan_module_source(
            "from telethon.tl.functions.account import ResetAuthorizationRequest\n"
            "request = ResetAuthorizationRequest(hash=1)\n",
            trusted=True,
        )

        self.assertTrue(report.allowed)
        self.assertEqual(report.verdict, "VERIFIED")


if __name__ == "__main__":
    unittest.main()
