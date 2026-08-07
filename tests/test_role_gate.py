from __future__ import annotations

import unittest
from unittest.mock import patch

from deathtg.role_gate import OWNER_TG_ID, can_assign_role


class RoleGateTests(unittest.TestCase):
    def test_non_owner_rechecks_existing_elevated_role(self) -> None:
        with patch("deathtg.role_gate.current_owner_id", return_value=123456):
            allowed, _message = can_assign_role(current_role="developer", requested_role="developer")
        self.assertFalse(allowed)

    def test_owner_can_assign_elevated_role(self) -> None:
        with patch("deathtg.role_gate.current_owner_id", return_value=OWNER_TG_ID):
            allowed, _message = can_assign_role(current_role="user", requested_role="admin")
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
