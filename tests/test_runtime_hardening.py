from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deathtg import state_db
from deathtg.modules.root import RootMod
from deathtg.panel import clean_actions, clean_core


class RootDiagnosticsSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_operators_and_python_execution_are_blocked(self) -> None:
        module = RootMod()
        for command in ("ls & whoami", "pwd; whoami", "python -c print(1)", "python3"):
            with self.subTest(command=command):
                allowed, _ = module._is_safe_terminal_command(command)
                self.assertFalse(allowed)

    async def test_safe_diagnostic_runs_without_shell(self) -> None:
        module = RootMod()
        allowed, _ = module._is_safe_terminal_command("pwd")
        self.assertTrue(allowed)
        self.assertTrue(await module._run_shell_command("pwd"))


class StateDatabaseSecurityTests(unittest.TestCase):
    def test_upsert_rejects_unknown_identifiers(self) -> None:
        with self.assertRaises(ValueError):
            state_db.upsert("settings; DROP TABLE settings", "key", "x", {"value": "y"})
        with self.assertRaises(ValueError):
            state_db.upsert("settings", "not_a_key", "x", {"value": "y"})

    def test_multiple_module_requirements_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "state.db"
            original_connect = state_db.connect

            def temporary_connect(*_args, **_kwargs):
                return original_connect(database)

            with patch.object(state_db, "connect", side_effect=temporary_connect):
                state_db.sync_module_requirements("Demo", ["aiohttp>=3", "Pillow>=10"])
                state_db.set_module_requirement_status("Demo", "aiohttp>=3", True)
                with state_db.connect() as connection:
                    rows = connection.execute(
                        "SELECT requirement, installed FROM module_requirements WHERE module_key=? ORDER BY requirement",
                        ("Demo",),
                    ).fetchall()
                self.assertEqual([row["requirement"] for row in rows], ["Pillow>=10", "aiohttp>=3"])
                self.assertEqual(dict((row["requirement"], row["installed"]) for row in rows)["aiohttp>=3"], 1)

                state_db.sync_module_requirements("Demo", ["Pillow>=10"])
                with state_db.connect() as connection:
                    remaining = connection.execute(
                        "SELECT requirement FROM module_requirements WHERE module_key=?",
                        ("Demo",),
                    ).fetchall()
                self.assertEqual([row["requirement"] for row in remaining], ["Pillow>=10"])


class PendingInstallSecurityTests(unittest.TestCase):
    def _report(self):
        return SimpleNamespace(
            severity="high",
            verdict="blocked",
            score=10,
            allowed=False,
            trusted=False,
            reasons=["blocked"],
            findings=[],
            pretty=lambda: "blocked",
        )

    def test_pending_override_is_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            clean_actions, "PENDING_INSTALLS_DIR", Path(tmp)
        ):
            token = clean_actions._save_pending_install(
                filename="Demo.py",
                source="print('demo')",
                link="https://example.com/Demo.py",
                source_type="url",
                trusted=False,
                report=self._report(),
            )
            self.assertIsNotNone(clean_actions._consume_pending_install(token))
            self.assertIsNone(clean_actions._consume_pending_install(token))

    def test_invalid_and_expired_pending_tokens_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            clean_actions, "PENDING_INSTALLS_DIR", Path(tmp)
        ):
            self.assertIsNone(clean_actions.load_pending_install("../../outside"))
            with patch.object(clean_actions.time, "time", return_value=1_000_000):
                token = clean_actions._save_pending_install(
                    filename="Demo.py",
                    source="print('demo')",
                    link="https://example.com/Demo.py",
                    source_type="url",
                    trusted=False,
                    report=self._report(),
                )
            with patch.object(
                clean_actions.time,
                "time",
                return_value=1_000_000 + clean_actions.PENDING_INSTALL_TTL + 1,
            ):
                self.assertIsNone(clean_actions.load_pending_install(token))

    def test_malformed_pending_timestamp_is_rejected_without_server_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            clean_actions, "PENDING_INSTALLS_DIR", Path(tmp)
        ):
            token = "A" * 24
            path = clean_actions._pending_path(token)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"created_at": "not-a-number"}', encoding="utf-8")

            self.assertIsNone(clean_actions.load_pending_install(token))
            self.assertFalse(path.exists())


class ModuleImageSecurityTests(unittest.TestCase):
    def test_module_image_url_is_restricted_to_safe_sources(self) -> None:
        self.assertEqual(clean_core._safe_module_image_url("javascript:alert(1)"), "")
        self.assertEqual(clean_core._safe_module_image_url("https://evil.example/module.png"), "")
        self.assertEqual(clean_core._safe_module_image_url("/images/modules/Module.png"), "/images/modules/Module.png")
        trusted = "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/Demo/Module.png"
        self.assertEqual(clean_core._safe_module_image_url(trusted), trusted)


if __name__ == "__main__":
    unittest.main()
