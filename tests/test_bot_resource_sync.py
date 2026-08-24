from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deathtg import startup_sync


class FakeConversation:
    def __init__(self, client) -> None:
        self.client = client
        self.responses = [
            SimpleNamespace(raw_text="No active command to cancel."),
            SimpleNamespace(raw_text="Choose a bot to change profile photo."),
            SimpleNamespace(raw_text="OK. Send me the new profile photo for the bot."),
            SimpleNamespace(raw_text="Success! Profile photo updated."),
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def send_message(self, message: str):
        self.client.sent_messages.append(message)

    async def get_response(self):
        return self.responses.pop(0)

    async def send_file(self, file_path: str):
        self.client.sent_files.append(file_path)


class FakeBotFatherClient:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.sent_files: list[str] = []

    async def get_input_entity(self, entity):
        return entity

    async def __call__(self, _request):
        return True

    def conversation(self, *_args, **_kwargs):
        return FakeConversation(self)


class BotResourceSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.avatar = self.root / "avatar.png"
        self.avatar.write_bytes(b"avatar-v1")
        self.patches = [
            patch.object(startup_sync, "RUNTIME_DIR", self.root),
            patch.object(startup_sync, "BOT_RESOURCE_STATE_PATH", self.root / "bot_resource_state.json"),
            patch.object(startup_sync, "BOT_AVATAR", self.avatar),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tempdir.cleanup()

    async def test_existing_avatar_is_adopted_without_botfather_noise(self) -> None:
        client = FakeBotFatherClient()

        ok, error = await startup_sync._sync_bot_avatar(client, "dtg2054091032_iabc123_bot", role="inline")

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(client.sent_messages, [])
        self.assertEqual(client.sent_files, [])

    async def test_changed_avatar_syncs_once_and_records_new_fingerprint(self) -> None:
        username = "dtg2054091032_iabc123_bot"
        startup_sync._mark_resource_current("avatar", "inline", username, "old")
        client = FakeBotFatherClient()

        ok, error = await startup_sync._sync_bot_avatar(client, username, role="inline")

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertIn("/setuserpic", client.sent_messages)
        self.assertIn(f"@{username}", client.sent_messages)
        self.assertEqual(client.sent_files, [str(self.avatar)])

        client2 = FakeBotFatherClient()
        ok2, error2 = await startup_sync._sync_bot_avatar(client2, username, role="inline")
        self.assertTrue(ok2)
        self.assertIsNone(error2)
        self.assertEqual(client2.sent_messages, [])

    async def test_transient_getme_error_keeps_token_and_skips_create(self) -> None:
        create = AsyncMock(return_value=("new-token", None))
        fetch = AsyncMock(return_value=("", "getMe HTTP 502"))

        with (
            patch.object(startup_sync, "_fetch_bot_username", fetch),
            patch.object(startup_sync, "_create_bot_with_botfather", create),
        ):
            token, status = await startup_sync._ensure_bot(
                object(),
                "123456:existing-token",
                2054091032,
                role="inline",
                allow_create=True,
            )

        self.assertEqual(token, "123456:existing-token")
        self.assertIn("skipped BotFather auto-create", status["error"])
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
