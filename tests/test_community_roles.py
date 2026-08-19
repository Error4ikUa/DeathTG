from __future__ import annotations

import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import deathtg.community_roles as roles


class CommunityRoleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original_paths = (
            roles.COMMUNITY_ROLES_DB_PATH,
            roles.COMMUNITY_REGISTRY_PATH,
            roles.ROLE_SCAN_RESULTS_DIR,
        )
        roles.COMMUNITY_ROLES_DB_PATH = root / "roles.sqlite3"
        roles.COMMUNITY_REGISTRY_PATH = root / "legacy.json"
        roles.ROLE_SCAN_RESULTS_DIR = root / "scan"

    def tearDown(self) -> None:
        (
            roles.COMMUNITY_ROLES_DB_PATH,
            roles.COMMUNITY_REGISTRY_PATH,
            roles.ROLE_SCAN_RESULTS_DIR,
        ) = self.original_paths
        self.temp.cleanup()

    def test_invite_is_single_use_and_persists_identity(self) -> None:
        invite = roles.issue_role_invite("developer", actor_id=roles.OWNER_TG_ID)
        result = roles.redeem_role_invite(
            str(invite["code"]),
            user_id=777001,
            username="dtg_tester",
            display_name="DTG Tester",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(roles.allowed_role(777001, "developer"))
        self.assertEqual(roles.list_role_entries()[0]["username"], "dtg_tester")

        reused = roles.redeem_role_invite(str(invite["code"]), user_id=777002)
        self.assertFalse(reused["ok"])

    def test_target_bound_invite_rejects_another_user(self) -> None:
        invite = roles.issue_role_invite(
            "admin",
            actor_id=roles.OWNER_TG_ID,
            target_user_id=900001,
        )

        denied = roles.redeem_role_invite(str(invite["code"]), user_id=900002)
        accepted = roles.redeem_role_invite(str(invite["code"]), user_id=900001)

        self.assertFalse(denied["ok"])
        self.assertTrue(accepted["ok"])
        self.assertTrue(roles.allowed_role(900001, "admin"))

    def test_only_owner_can_issue_or_revoke(self) -> None:
        with self.assertRaises(PermissionError):
            roles.issue_role_invite("admin", actor_id=123456)

        roles.grant_role(800001, "admin", actor_id=roles.OWNER_TG_ID)
        with self.assertRaises(PermissionError):
            roles.revoke_role(800001, "admin", actor_id=123456)
        roles.revoke_role(800001, "admin", actor_id=roles.OWNER_TG_ID)
        self.assertFalse(roles.allowed_role(800001, "admin"))

    def test_internal_grant_helpers_require_explicit_owner_actor(self) -> None:
        with self.assertRaises(TypeError):
            roles.grant_role(800001, "admin")
        with self.assertRaises(TypeError):
            roles.revoke_role(800001, "admin")

    def test_concurrent_redemption_has_exactly_one_winner(self) -> None:
        invite = roles.issue_role_invite("developer", actor_id=roles.OWNER_TG_ID)
        barrier = threading.Barrier(2)
        results: list[dict[str, object]] = []

        def redeem(user_id: int) -> None:
            barrier.wait()
            results.append(roles.redeem_role_invite(str(invite["code"]), user_id=user_id))

        threads = [threading.Thread(target=redeem, args=(user_id,)) for user_id in (810001, 810002)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(bool(item["ok"]) for item in results), 1)
        self.assertEqual(sum(roles.allowed_role(user_id, "developer") for user_id in (810001, 810002)), 1)

    def test_role_scan_result_rejects_unsafe_or_empty_request_ids(self) -> None:
        for value in ("", "../escape", "short", "with.dot"):
            with self.assertRaises(ValueError):
                roles.role_scan_result_path(value)

    def test_non_owner_uses_central_role_bot_not_local_generated_bot(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OWNER_ID": "5637167748",
                "COMMUNITY_BOT_USERNAME": "dtg5637167748_missing_bot",
                "DEATHTG_ROLE_BOT_USERNAME": "",
            },
            clear=False,
        ):
            username = roles.preferred_community_bot_username(5637167748)

        self.assertEqual(username, roles.DEFAULT_COMMUNITY_BOT_USERNAME)

    def test_owner_uses_own_configured_community_bot(self) -> None:
        with patch.dict(
            "os.environ",
            {"COMMUNITY_BOT_USERNAME": "dtg2054091032_owner_bot"},
            clear=False,
        ):
            username = roles.preferred_community_bot_username(roles.OWNER_TG_ID)

        self.assertEqual(username, "dtg2054091032_owner_bot")


if __name__ == "__main__":
    unittest.main()
