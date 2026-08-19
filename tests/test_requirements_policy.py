from __future__ import annotations

import unittest
from unittest.mock import patch

from deathtg.requirements_manager import install_requirements, safe_requirements


class RequirementPolicyTests(unittest.TestCase):
    def test_internal_compatibility_namespaces_never_reach_pip(self) -> None:
        safe, skipped = safe_requirements(
            ["deathtg", "Death-TG>=2.0", "hikka", "hikariatama", "aiohttp>=3.9"]
        )

        self.assertEqual(safe, ["aiohttp>=3.9"])
        self.assertEqual(len(skipped), 4)

    def test_normal_version_ranges_and_extras_remain_installable(self) -> None:
        safe, skipped = safe_requirements(
            ["aiohttp[speedups]>=3.9,<4", "Pillow~=12.0", "qrcode!=8.1"]
        )

        self.assertEqual(
            safe,
            ["aiohttp[speedups]>=3.9,<4", "Pillow~=12.0", "qrcode!=8.1"],
        )
        self.assertEqual(skipped, [])

    def test_unsafe_or_internal_only_list_does_not_launch_pip(self) -> None:
        with patch("deathtg.requirements_manager.subprocess.run") as run:
            result = install_requirements(["deathtg", "package; rm -rf /"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["installed"], [])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
