from __future__ import annotations

import io
import unittest
import zipfile
from unittest.mock import patch

from deathtg import module_repo


class ModuleRepositorySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_module_url_is_rejected_before_request(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "public HTTPS"):
            await module_repo.fetch_module_bundle("http://127.0.0.1:8080/private.py")

    async def test_https_private_ip_is_rejected_before_request(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "private or reserved"):
            await module_repo.fetch_module_bundle("https://169.254.169.254/latest.py")

    async def test_url_credentials_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "credentials"):
            await module_repo.fetch_module_bundle("https://user:pass@example.com/module.py")

    def test_repository_archive_member_limit_is_enforced(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("repo-main/One/One.py", "print(1)")
            archive.writestr("repo-main/Two/Two.py", "print(2)")
        with patch.object(module_repo, "MAX_REPO_ARCHIVE_MEMBERS", 1):
            with self.assertRaisesRegex(RuntimeError, "too many files"):
                module_repo._zip_module_items(payload.getvalue(), "owner", "repo", "main")

    def test_repository_archive_ignores_test_and_tooling_folders(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("repo-main/RealModule/RealModule.py", "VALUE = 1")
            archive.writestr("repo-main/tests/test_module.py", "VALUE = 2")
            archive.writestr("repo-main/scripts/release.py", "VALUE = 3")

        items = module_repo._zip_module_items(payload.getvalue(), "owner", "repo", "main")

        self.assertEqual([item["name"] for item in items], ["RealModule"])

    def test_trusted_repository_check_validates_host_and_path(self) -> None:
        self.assertTrue(
            module_repo.trusted_repo_link(
                "https://github.com/Error4ikUa/DTG_Modules/tree/main/NoteDtg"
            )
        )
        self.assertFalse(
            module_repo.trusted_repo_link(
                "https://evil.example/github.com/error4ikua/dtg_modules/tree/main/NoteDtg"
            )
        )
        self.assertFalse(
            module_repo.trusted_repo_link(
                "https://github.com.evil.example/Error4ikUa/DTG_Modules/tree/main/NoteDtg"
            )
        )


if __name__ == "__main__":
    unittest.main()
