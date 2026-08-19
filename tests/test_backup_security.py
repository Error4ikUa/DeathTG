from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from deathtg import backup_manager


class BackupSecurityTests(unittest.TestCase):
    def test_restore_rejects_path_traversal_before_writing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "attack.dtgbak"
            modules_dir = root / "modules"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("modules/Good/Good.py", "print('ok')")
                archive.writestr("modules/../../outside.py", "owned")

            with patch.object(backup_manager, "MODULES_DIR", modules_dir):
                result = backup_manager.restore_modules_backup(archive_path)

            self.assertFalse(result["ok"])
            self.assertFalse((modules_dir / "Good" / "Good.py").exists())
            self.assertFalse((root / "outside.py").exists())

    def test_restore_rejects_oversized_expanded_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "bomb.dtgbak"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("modules/Bomb/Bomb.py", b"x" * 128)

            with (
                patch.object(backup_manager, "MODULES_DIR", root / "modules"),
                patch.object(backup_manager, "MAX_BACKUP_MEMBER_BYTES", 64),
            ):
                result = backup_manager.restore_modules_backup(archive_path)

            self.assertFalse(result["ok"])
            self.assertIn("too large", result["message"].lower())

    def test_restore_streams_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "valid.dtgbak"
            modules_dir = root / "modules"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("modules/Good/Good.py", "print('ok')")

            with patch.object(backup_manager, "MODULES_DIR", modules_dir):
                result = backup_manager.restore_modules_backup(archive_path)

            self.assertTrue(result["ok"])
            self.assertEqual((modules_dir / "Good" / "Good.py").read_text(), "print('ok')")


if __name__ == "__main__":
    unittest.main()
