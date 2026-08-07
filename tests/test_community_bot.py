from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import deathtg.community_roles as roles
from deathtg.community_bot import CommunityBotService


class FakeEvent:
    def __init__(self, text: str, sender_id: int, *, username: str = "", first_name: str = "User") -> None:
        self.raw_text = text
        self.sender_id = sender_id
        self.sender = SimpleNamespace(
            id=sender_id,
            username=username,
            first_name=first_name,
            last_name="",
        )
        self.replies: list[tuple[str, dict]] = []

    async def reply(self, text: str, **kwargs) -> None:
        self.replies.append((text, kwargs))

    async def get_sender(self):
        return self.sender


class CommunityBotCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original_paths = (
            roles.COMMUNITY_ROLES_DB_PATH,
            roles.COMMUNITY_REGISTRY_PATH,
        )
        roles.COMMUNITY_ROLES_DB_PATH = root / "roles.sqlite3"
        roles.COMMUNITY_REGISTRY_PATH = root / "legacy.json"
        self.service = CommunityBotService(api_id=1, api_hash="test")

    async def asyncTearDown(self) -> None:
        roles.COMMUNITY_ROLES_DB_PATH, roles.COMMUNITY_REGISTRY_PATH = self.original_paths
        self.temp.cleanup()

    async def test_owner_issues_user_redeems_and_registry_lists_identity(self) -> None:
        owner_event = FakeEvent("/aduserdev", roles.OWNER_TG_ID)
        await self.service._on_message(owner_event)
        match = re.search(r"DTG-DEV-[A-Z0-9-]+", owner_event.replies[0][0])
        self.assertIsNotNone(match)

        user_event = FakeEvent(match.group(0), 700001, username="developer_one", first_name="Developer")
        await self.service._on_message(user_event)
        self.assertIn("успешно", user_event.replies[0][0].lower())
        self.assertTrue(roles.allowed_role(700001, "developer"))

        list_event = FakeEvent("/userinfo", roles.OWNER_TG_ID)
        await self.service._on_message(list_event)
        self.assertIn("@developer_one", list_event.replies[0][0])
        self.assertIn("700001", list_event.replies[0][0])

    async def test_non_owner_cannot_issue_or_revoke_roles(self) -> None:
        issue_event = FakeEvent("/aduseradm", 700002)
        await self.service._on_message(issue_event)
        self.assertFalse(roles.list_role_entries())
        self.assertIn("one-time", issue_event.replies[0][0])

        roles.grant_role(700003, "admin", actor_id=roles.OWNER_TG_ID)
        revoke_event = FakeEvent("/deluseradm 700003", 700002)
        await self.service._on_message(revoke_event)
        self.assertTrue(roles.allowed_role(700003, "admin"))

    async def test_role_scan_cannot_be_spoofed_for_another_user(self) -> None:
        roles.grant_role(700004, "developer", actor_id=roles.OWNER_TG_ID)

        spoofed = FakeEvent("/scan 700004 developer", 799999)
        await self.service._on_message(spoofed)
        self.assertEqual(spoofed.replies[0][0], "false")

        legitimate = FakeEvent("/scan 700004 developer", 700004)
        await self.service._on_message(legitimate)
        self.assertEqual(legitimate.replies[0][0], "true")


if __name__ == "__main__":
    unittest.main()
