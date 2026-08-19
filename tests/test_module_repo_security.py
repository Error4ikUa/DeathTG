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


if __name__ == "__main__":
    unittest.main()
