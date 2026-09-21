from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from deathtg.panel import clean_actions


class _Loader:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.loaded_paths: list[Path] = []

    async def load_file(self, path: Path, **_kwargs) -> str:
        self.loaded_paths.append(path)
        if self.error:
            raise self.error
        return "Demo"


class ModuleInstallTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_folder_update_preserves_previous_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modules = Path(tmp)
            current = modules / "Demo"
            current.mkdir()
            (current / "Demo.py").write_text("VALUE = 'old'\n", encoding="utf-8")
            with (
                patch.object(clean_actions, "MODULES_DIR", modules),
                patch.object(clean_actions, "refresh_modules", AsyncMock()),
            ):
                with self.assertRaisesRegex(RuntimeError, "syntax error"):
                    await clean_actions._install_module_source(
                        filename="Demo.py",
                        source="def broken(:\n",
                        link="https://github.com/Error4ikUa/DTG_Modules/tree/main/Demo",
                        source_type="repo",
                        trusted=True,
                        install_kind="folder",
                        module_name="Demo",
                    )

            self.assertEqual((current / "Demo.py").read_text(encoding="utf-8"), "VALUE = 'old'\n")
            self.assertFalse(any("stage" in path.name or "rollback" in path.name for path in modules.iterdir()))

    async def test_successful_folder_update_atomically_replaces_previous_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modules = Path(tmp)
            current = modules / "Demo"
            current.mkdir()
            (current / "Demo.py").write_text("VALUE = 'old'\n", encoding="utf-8")
            with (
                patch.object(clean_actions, "MODULES_DIR", modules),
                patch.object(clean_actions, "refresh_modules", AsyncMock()),
                patch.object(clean_actions, "_set_module_meta"),
                patch.object(clean_actions, "_queue_userbot_action"),
            ):
                name = await clean_actions._install_module_source(
                    filename="Demo.py",
                    source="VALUE = 'new'\n",
                    link="https://github.com/Error4ikUa/DTG_Modules/tree/main/Demo",
                    source_type="repo",
                    trusted=True,
                    install_kind="folder",
                    module_name="Demo",
                )

            self.assertEqual(name, "Demo")
            self.assertEqual((current / "Demo.py").read_text(encoding="utf-8"), "VALUE = 'new'\n")
            self.assertFalse(any("stage" in path.name or "rollback" in path.name for path in modules.iterdir()))

    async def test_clean_untrusted_module_still_requires_owner_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modules = Path(tmp)
            with (
                patch.object(clean_actions, "MODULES_DIR", modules),
                patch.object(clean_actions, "_save_pending_install", return_value="safe_pending_token_12345"),
            ):
                with self.assertRaisesRegex(RuntimeError, "SECURITY_PENDING:safe_pending_token_12345"):
                    await clean_actions._install_module_source(
                        filename="Demo.py",
                        source="VALUE = 1\n",
                        link="https://example.com/Demo.py",
                        source_type="url",
                        trusted=False,
                    )

            self.assertEqual(list(modules.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
