from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deathtg import session_guard


class SessionPathSecurityTests(unittest.TestCase):
    def test_session_name_cannot_escape_project_root(self) -> None:
        for value in ("../outside", "folder/session", r"folder\session", "C:/outside", ".."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                session_guard.session_main_file(value)

    def test_simple_session_name_remains_supported(self) -> None:
        with patch.object(session_guard, "ROOT_DIR", Path("C:/DeathTG")):
            self.assertEqual(session_guard.session_main_file("twink-01"), Path("C:/DeathTG/twink-01.session"))

    def test_snapshot_manifest_cannot_restore_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            backups = root / "runtime" / "session_backups"
            snapshot = backups / "snapshot"
            snapshot.mkdir(parents=True)
            outside = Path(temp) / "outside.txt"
            (snapshot / "payload").write_text("owned", encoding="utf-8")
            (snapshot / "manifest.json").write_text(
                json.dumps({"files": [{"source": "../outside.txt", "backup": "payload"}]}),
                encoding="utf-8",
            )
            with (
                patch.object(session_guard, "ROOT_DIR", root),
                patch.object(session_guard, "SESSION_BACKUP_DIR", backups),
            ):
                result = session_guard.restore_private_snapshot(snapshot, overwrite=True)

            self.assertTrue(result["ok"])
            self.assertEqual(result["restored"], 0)
            self.assertFalse(outside.exists())

    def test_new_bot_session_directory_is_included_in_private_snapshot_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            bot_sessions = runtime / "bot_sessions"
            bot_sessions.mkdir(parents=True)
            main_session = root / "deathtg.session"
            bot_session = bot_sessions / "inline_123.session"
            main_session.write_bytes(b"main")
            bot_session.write_bytes(b"bot")
            with (
                patch.object(session_guard, "ROOT_DIR", root),
                patch.object(session_guard, "RUNTIME_DIR", runtime),
                patch.object(session_guard, "ENV_PATH", root / ".env"),
                patch.object(session_guard, "SESSION_BACKUP_DIR", runtime / "session_backups"),
            ):
                files = {path.resolve() for path in session_guard.private_runtime_files("deathtg")}

            self.assertIn(main_session.resolve(), files)
            self.assertIn(bot_session.resolve(), files)


if __name__ == "__main__":
    unittest.main()
