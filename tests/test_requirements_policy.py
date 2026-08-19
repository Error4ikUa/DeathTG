from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from deathtg.health_tools import _requirements_from_entry
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
            result = install_requirements(
                ["deathtg", "package; rm -rf /", "--index-url", "-e", "--extra-index-url=https://evil.example"]
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["installed"], [])
        run.assert_not_called()

    def test_health_repair_uses_the_same_requirement_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / "Demo.py"
            entry.write_text("# requires: deathtg aiohttp>=3.9 --extra-index-url\n", encoding="utf-8")
            self.assertEqual(_requirements_from_entry(entry), ["aiohttp>=3.9"])


if __name__ == "__main__":
    unittest.main()
