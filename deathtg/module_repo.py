from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import aiohttp


MODULE_REPO_INDEX = os.getenv(
    "MODULE_REPO_INDEX",
    "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/index.json",
)
MODULE_REPO_API = os.getenv(
    "MODULE_REPO_API",
    "https://api.github.com/repos/Error4ikUa/DTG_Modules/contents?ref=main",
)


def normalize_github_raw_url(link: str) -> str:
    value = (link or "").strip()
    if not value:
        return ""
    if "github.com" in value and "/blob/" in value:
        return value.replace("github.com/", "raw.githubusercontent.com/").replace("/blob/", "/")
    if "gitlab.com" in value and "/-/blob/" in value:
        return value.replace("/-/blob/", "/-/raw/")
    return value


def is_url(value: str) -> bool:
    parsed = urlparse((value or "").strip())
    return bool(parsed.scheme and parsed.netloc)


def trusted_repo_link(link: str) -> bool:
    raw = normalize_github_raw_url(link).lower()
    return (
        "raw.githubusercontent.com/error4ikua/dtg_modules/" in raw
        or "github.com/error4ikua/dtg_modules/" in raw
        or "api.github.com/repos/error4ikua/dtg_modules/" in raw
    )


def _normalize_repo_item(item: dict) -> dict:
    link = normalize_github_raw_url(
        str(item.get("link") or item.get("raw") or item.get("url") or item.get("download_url") or "")
    )
    image = str(item.get("image") or item.get("Image") or item.get("Module.png") or item.get("Image.png") or "")
    name = str(item.get("name") or Path(link.split("?", 1)[0]).stem or "module")
    description = str(item.get("description") or f"{name} module from DTG_Modules")
    return {
        **item,
        "name": name,
        "description": description,
        "image": image,
        "modul_png": image,
        "link": link,
        "author": str(item.get("author") or "DTG"),
        "version": str(item.get("version") or "latest"),
        "verified": trusted_repo_link(link),
    }


async def _from_index(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(MODULE_REPO_INDEX, timeout=12) as response:
        if response.status != 200:
            return []
        data = await response.json()
    items = data.get("modules", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [_normalize_repo_item(dict(item)) for item in items if isinstance(item, dict)]


async def _from_github_contents(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(MODULE_REPO_API, timeout=12) as response:
        if response.status != 200:
            return []
        data = await response.json()
    if not isinstance(data, list):
        return []

    modules: list[dict] = []
    for item in data:
        name = str(item.get("name") or "")
        item_type = str(item.get("type") or "")
        if item_type == "dir":
            dir_url = str(item.get("url") or "")
            if not dir_url:
                continue
            async with session.get(dir_url, timeout=12) as sub_response:
                if sub_response.status != 200:
                    continue
                sub_items = await sub_response.json()
            if not isinstance(sub_items, list):
                continue
            py_item = next(
                (
                    sub
                    for sub in sub_items
                    if str(sub.get("name") or "").endswith(".py")
                    and not str(sub.get("name") or "").startswith("_")
                ),
                None,
            )
            if not py_item:
                continue
            image_item = next(
                (
                    sub
                    for sub in sub_items
                    if str(sub.get("name") or "").lower() == "module.png"
                ),
                None,
            )
            modules.append(
                _normalize_repo_item(
                    {
                        "name": name or Path(str(py_item.get("name") or "module.py")).stem,
                        "description": f"{name or Path(str(py_item.get('name') or 'module.py')).stem} module from DTG_Modules",
                        "image": image_item.get("download_url") if image_item else "",
                        "link": py_item.get("download_url") or py_item.get("html_url") or "",
                    }
                )
            )
            continue

        if name.endswith(".py") and not name.startswith("_"):
            stem = name[:-3]
            modules.append(
                _normalize_repo_item(
                    {
                        "name": stem,
                        "description": f"{stem} module from DTG_Modules",
                        "link": item.get("download_url") or item.get("html_url") or "",
                    }
                )
            )
    return modules


async def fetch_repo_modules() -> list[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            items = await _from_index(session)
            if not items:
                items = await _from_github_contents(session)
    except Exception:
        return []
    unique: dict[str, dict] = {}
    for item in items:
        key = str(item.get("name") or "").strip().lower()
        if key and key not in unique:
            unique[key] = item
    return sorted(unique.values(), key=lambda item: str(item.get("name") or "").lower())


async def find_repo_module(query: str) -> dict | None:
    needle = (query or "").strip().lower()
    if not needle:
        return None
    for item in await fetch_repo_modules():
        name = str(item.get("name") or "").strip().lower()
        link = str(item.get("link") or "").strip().lower()
        if name == needle or Path(link.split("?", 1)[0]).stem.lower() == needle:
            return item
    return None
