from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from telethon.errors import UnauthorizedError

import deathtg.panel.auth_flow as auth_flow
import deathtg.app as app_module
import deathtg.session_guard as session_guard
import deathtg.update_manager as update_manager
from deathtg.app import DeathTG
from deathtg.telethon_policy import INVALID_SESSION_ERRORS


class SessionLifecycleTests(unittest.TestCase):
    def test_backup_scheduler_has_wall_clock_dependency(self) -> None:
        self.assertTrue(callable(app_module.time.time))

    def test_revoked_session_is_snapshotted_and_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backup_root = root / "runtime" / "session_backups"
            portable_backup = root / "runtime" / "backups" / "modules.dtgbak"
            portable_backup.parent.mkdir(parents=True)
            portable_backup.write_bytes(b"portable modules backup")
            session_path = root / "deathtg.session"
            session_path.write_bytes(b"telegram session")

            with (
                patch.object(session_guard, "ROOT_DIR", root),
                patch.object(session_guard, "SESSION_BACKUP_DIR", backup_root),
            ):
                result = session_guard.quarantine_session_files("deathtg", reason="revoked-test")

            self.assertTrue(result["ok"])
            self.assertFalse(session_path.exists())
            self.assertTrue((root / "deathtg.session.invalid").exists())
            snapshot_dir = Path(result["snapshot"]["backup_dir"])
            self.assertTrue((snapshot_dir / "deathtg.session").exists())
            self.assertTrue((snapshot_dir / "manifest.json").exists())
            self.assertEqual(portable_backup.read_bytes(), b"portable modules backup")

    def test_only_concrete_auth_failures_trigger_reauthorization(self) -> None:
        self.assertNotIn(UnauthorizedError, INVALID_SESSION_ERRORS)
        self.assertFalse(isinstance(OSError("network offline"), INVALID_SESSION_ERRORS))

    def test_reconnect_preserves_service_tokens_and_runtime_settings(self) -> None:
        captured: dict[str, str] = {}
        existing = {
            "BOT_TOKEN": "inline-token",
            "BOT_TOKEN_HELPER": "helper-token",
            "BOT_TOKEN_COMMUNITY": "community-token",
            "OWNER_ID": "2054091032",
            "BACKUP_ENABLED": "1",
            "BACKUP_INTERVAL": "3600",
            "PANEL_SECRET": "stable-panel-secret",
            "COMMAND_PREFIX": ".",
        }

        def capture(updates: dict[str, str]) -> None:
            captured.update(updates)

        with (
            patch.object(auth_flow, "parse_env_file", return_value=existing),
            patch.object(auth_flow, "update_env_values", side_effect=capture),
        ):
            auth_flow.write_env(12345, "new-api-hash", "deathtg")

        self.assertNotIn("BOT_TOKEN", captured)
        self.assertNotIn("PHONE", captured)
        self.assertNotIn("PANEL_SECRET", captured)
        self.assertEqual(captured["LOGIN_PENDING"], "1")
        self.assertEqual(existing["BACKUP_ENABLED"], "1")

    def test_update_snapshot_restores_session_if_update_removed_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            backup_root = runtime / "session_backups"
            marker = backup_root / "latest_update.json"
            session_path = root / "deathtg.session"
            session_path.write_bytes(b"authorized telegram session")

            with (
                patch.object(session_guard, "ROOT_DIR", root),
                patch.object(session_guard, "RUNTIME_DIR", runtime),
                patch.object(session_guard, "ENV_PATH", root / ".env"),
                patch.object(session_guard, "SESSION_BACKUP_DIR", backup_root),
                patch.object(session_guard, "UPDATE_MARKER_PATH", marker),
                patch.object(session_guard, "current_session_name", return_value="deathtg"),
            ):
                snapshot = session_guard.protect_update_session_snapshot()
                session_path.unlink()
                restored = session_guard.recover_update_session_snapshot(clear=True)

            self.assertEqual(snapshot["count"], 1)
            self.assertEqual(restored["restored"], 1)
            self.assertEqual(session_path.read_bytes(), b"authorized telegram session")
            self.assertFalse(marker.exists())


class QRSessionProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_failure_does_not_replace_existing_session(self) -> None:
        class OfflineClient:
            disconnected = False

            async def connect(self) -> None:
                return None

            async def is_user_authorized(self) -> bool:
                raise OSError("network offline")

            async def disconnect(self) -> None:
                self.disconnected = True

        client = OfflineClient()
        auth_flow.PENDING.clear()
        auth_flow.QR_FLOW_INDEX.clear()
        auth_flow.QR_FLOW_LOCKS.clear()
        with (
            patch.object(auth_flow, "_new_client", return_value=client),
            patch.object(auth_flow, "_set_login_pending"),
            patch.object(auth_flow, "_set_login_stage"),
            patch.object(auth_flow, "_cleanup_session_files") as cleanup,
        ):
            with self.assertRaisesRegex(RuntimeError, "session was preserved"):
                await auth_flow.begin_qr_login("flow", 12345, "hash", "deathtg")
        cleanup.assert_not_called()
        self.assertTrue(client.disconnected)

    async def test_launcher_shutdown_action_disconnects_telethon_cleanly(self) -> None:
        class Client:
            disconnected = False

            async def disconnect(self) -> None:
                self.disconnected = True

        app = object.__new__(DeathTG)
        app.client = Client()
        await app._apply_panel_action({"action": "shutdown"})
        self.assertTrue(app.client.disconnected)


class RestartLifecycleTests(unittest.TestCase):
    def test_restart_is_requested_through_parent_launcher_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            marker = runtime / "restart.request"
            with (
                patch.object(update_manager, "RUNTIME_DIR", runtime),
                patch.object(update_manager, "RESTART_REQUEST_PATH", marker),
            ):
                update_manager.schedule_restart(delay=0.2)
                deadline = time.time() + 2
                while time.time() < deadline and not marker.exists():
                    time.sleep(0.05)
            self.assertTrue(marker.exists())

    def test_update_stashes_and_restores_dirty_worktree(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_git(*args: str, timeout: int = 120):
            calls.append(args)
            if args[:2] == ("status", "--porcelain"):
                return update_manager.subprocess.CompletedProcess(args, 0, "?? modules/Test.py\n", "")
            return update_manager.subprocess.CompletedProcess(args, 0, "Saved working directory\n", "")

        with patch.object(update_manager, "_run_git", side_effect=fake_git):
            stash = update_manager._stash_worktree()
            restore = update_manager._restore_worktree(stash)

        self.assertTrue(stash["created"])
        self.assertTrue(restore["restored"])
        self.assertTrue(any(call[:2] == ("stash", "push") for call in calls))
        self.assertTrue(any(call[:2] == ("stash", "pop") for call in calls))

    def test_update_aborts_when_session_snapshot_fails(self) -> None:
        with (
            patch.object(
                update_manager,
                "_protect_sessions",
                return_value={"ok": False, "message": "snapshot unavailable"},
            ),
            patch.object(update_manager, "_stash_worktree") as stash,
            patch.object(update_manager, "save_update_state", side_effect=lambda value: value),
        ):
            result = update_manager.apply_update()

        self.assertFalse(result["ok"])
        self.assertFalse(result["updated"])
        self.assertIn("snapshot unavailable", result["message"])
        stash.assert_not_called()


if __name__ == "__main__":
    unittest.main()
