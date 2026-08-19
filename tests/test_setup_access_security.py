from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import deathtg.setup_access as setup_access


class SetupAccessSecurityTests(unittest.TestCase):
    def test_setup_token_is_atomic_and_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "setup_token.txt"
            with patch.object(setup_access, "SETUP_TOKEN_PATH", token_path):
                token = setup_access.ensure_setup_token()

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: setup_access.consume_setup_token(token), range(2)))

                self.assertEqual(results.count(True), 1)
                self.assertEqual(results.count(False), 1)
                self.assertFalse(setup_access.valid_setup_token(token))

    def test_expired_setup_token_is_rejected_and_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "setup_token.txt"
            with (
                patch.object(setup_access, "SETUP_TOKEN_PATH", token_path),
                patch.object(setup_access.time, "time", return_value=1_000_000),
            ):
                old_token = setup_access.ensure_setup_token()
            with (
                patch.object(setup_access, "SETUP_TOKEN_PATH", token_path),
                patch.object(setup_access.time, "time", return_value=1_000_000 + 7 * 60 * 60),
            ):
                self.assertFalse(setup_access.valid_setup_token(old_token))
                self.assertNotEqual(setup_access.ensure_setup_token(), old_token)


if __name__ == "__main__":
    unittest.main()
