from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
