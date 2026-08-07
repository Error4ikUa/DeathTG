from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import deathtg.module_repo as module_repo


def _items(count: int) -> list[dict]:
    return [
        {
            "name": f"Module{index}",
            "link": f"https://example.invalid/Module{index}.py",
            "raw_link": f"https://example.invalid/Module{index}.py",
        }
        for index in range(count)
    ]


class ModuleRepoCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.original_cache = module_repo.MODULE_REPO_CACHE_PATH
        module_repo.MODULE_REPO_CACHE_PATH = Path(self.temp.name) / "repo-cache.json"

    async def asyncTearDown(self) -> None:
        module_repo.MODULE_REPO_CACHE_PATH = self.original_cache
        self.temp.cleanup()

    async def test_partial_remote_response_does_not_shrink_catalog(self) -> None:
        module_repo._write_repo_cache(_items(8))
        with (
            patch.object(module_repo, "_from_index", AsyncMock(return_value=_items(1))),
            patch.object(module_repo, "_from_github_zip_archive", AsyncMock(return_value=[])),
        ):
            result = await module_repo.fetch_repo_modules(refresh=True)
        self.assertEqual(len(result), 8)

    async def test_index_and_archive_are_merged(self) -> None:
        index_item = {**_items(1)[0], "description": "Rich index metadata"}
        archive_items = _items(8)
        archive_items[0]["image"] = "https://example.invalid/Module.png"
        archive_items[0]["modul_png"] = archive_items[0]["image"]
        with (
            patch.object(module_repo, "_from_index", AsyncMock(return_value=[index_item])),
            patch.object(module_repo, "_from_github_zip_archive", AsyncMock(return_value=archive_items)),
        ):
            result = await module_repo.fetch_repo_modules(refresh=True)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[0]["description"], "Rich index metadata")
        self.assertEqual(result[0]["image"], "https://example.invalid/Module.png")

    async def test_fresh_cache_skips_remote_request(self) -> None:
        module_repo._write_repo_cache(_items(8))
        remote = AsyncMock(side_effect=AssertionError("remote should not be called"))
        with patch.object(module_repo, "_from_index", remote):
            result = await module_repo.fetch_repo_modules()
        self.assertEqual(len(result), 8)
        remote.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
