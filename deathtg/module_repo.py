from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
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

GITHUB_TREE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/tree/(?P<ref>[^/]+)/(?P<path>.+)$",
    re.I,
)
GITHUB_BLOB_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>.+)$",
    re.I,
)
GITHUB_RAW_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<ref>[^/]+)/(?P<path>.+)$",
    re.I,
)
GITHUB_CONTENTS_RE = re.compile(
    r"^https?://api\.github\.com/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/contents(?:/(?P<path>[^?]+))?(?:\?ref=(?P<ref>[^#]+))?$",
    re.I,
)


def _normalize_url(value: str) -> str:
    value = (value or "").strip().strip("'\"")
    if value.startswith("www."):
        return "https://" + value
    if value.startswith("github.com/") or value.startswith("raw.githubusercontent.com/") or value.startswith("api.github.com/"):
        return "https://" + value
    return value


def normalize_github_raw_url(link: str) -> str:
    value = _normalize_url(link)
    if not value:
        return ""
    if "github.com" in value and "/blob/" in value:
        return value.replace("github.com/", "raw.githubusercontent.com/").replace("/blob/", "/")
    if "gitlab.com" in value and "/-/blob/" in value:
        return value.replace("/-/blob/", "/-/raw/")
    return value


def is_url(value: str) -> bool:
    parsed = urlparse(_normalize_url(value))
    return bool(parsed.scheme and parsed.netloc)


def trusted_repo_link(link: str) -> bool:
    raw = _normalize_url(link).lower()
    return (
        "raw.githubusercontent.com/error4ikua/dtg_modules/" in raw
        or "github.com/error4ikua/dtg_modules/" in raw
        or "api.github.com/repos/error4ikua/dtg_modules/" in raw
    )


def _derive_tree_link_from_raw(raw_link: str) -> str:
    parsed = _parse_github_link(raw_link)
    if not parsed or parsed.get("kind") != "raw":
        return ""
    raw_path = str(parsed.get("path") or "").strip("/")
    parent = str(PurePosixPath(raw_path).parent)
    if not parent or parent == ".":
        return ""
    return _github_tree_url(parsed["owner"], parsed["repo"], parsed["ref"], parent)


def parse_requirements_text(text: str) -> list[str]:
    requirements: list[str] = []
    for line in (text or "").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        requirements.append(item)
    return sorted(set(requirements))


def _github_contents_url(owner: str, repo: str, path: str, ref: str) -> str:
    clean = path.strip("/")
    if clean:
        return f"https://api.github.com/repos/{owner}/{repo}/contents/{clean}?ref={ref}"
    return f"https://api.github.com/repos/{owner}/{repo}/contents?ref={ref}"


def _github_tree_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://github.com/{owner}/{repo}/tree/{ref}/{path.strip('/')}"


def _parse_github_link(link: str) -> dict | None:
    value = _normalize_url(link)
    for pattern, kind in (
        (GITHUB_TREE_RE, "tree"),
        (GITHUB_BLOB_RE, "blob"),
        (GITHUB_RAW_RE, "raw"),
        (GITHUB_CONTENTS_RE, "contents"),
    ):
        match = pattern.match(value)
        if match:
            payload = match.groupdict()
            payload["kind"] = kind
            payload["path"] = (payload.get("path") or "").strip("/")
            payload["ref"] = (payload.get("ref") or "main").strip("/")
            payload["owner"] = (payload.get("owner") or "").strip("/")
            payload["repo"] = (payload.get("repo") or "").strip("/")
            payload["url"] = value
            return payload
    return None


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"Download failed, HTTP {response.status}")
        return await response.text()


async def _fetch_json(session: aiohttp.ClientSession, url: str):
    async with session.get(url, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub API failed, HTTP {response.status}")
        return await response.json()


def _pick_python_file(items: list[dict], folder_name: str) -> dict | None:
    preferred = [
        f"{folder_name}.py",
        "main.py",
        "__init__.py",
    ]
    by_name = {str(item.get("name") or ""): item for item in items}
    for candidate in preferred:
        if candidate in by_name:
            return by_name[candidate]
    for item in items:
        name = str(item.get("name") or "")
        if name.endswith(".py") and not name.startswith("_"):
            return item
    return None


async def _fetch_folder_bundle(session: aiohttp.ClientSession, owner: str, repo: str, ref: str, path: str) -> dict:
    listing_url = _github_contents_url(owner, repo, path, ref)
    listing = await _fetch_json(session, listing_url)
    if not isinstance(listing, list):
        raise RuntimeError("Folder link did not return a module directory")
    folder_name = PurePosixPath(path).name or "module"
    py_item = _pick_python_file(listing, folder_name)
    if not py_item:
        raise RuntimeError("Folder does not contain a Python module entry")
    entry_name = str(py_item.get("name") or f"{folder_name}.py")
    raw_url = str(py_item.get("download_url") or py_item.get("html_url") or "")
    if not raw_url:
        raise RuntimeError("Python module file has no downloadable URL")
    source = await _fetch_text(session, normalize_github_raw_url(raw_url))
    image_item = next(
        (
            item
            for item in listing
            if str(item.get("name") or "").lower() in {"module.png", "image.png"}
        ),
        None,
    )
    requirements_item = next(
        (
            item
            for item in listing
            if str(item.get("name") or "").lower() == "requirements.txt"
        ),
        None,
    )
    requirements_text = ""
    requirements_url = ""
    if requirements_item:
        requirements_url = str(requirements_item.get("download_url") or "")
        if requirements_url:
            try:
                requirements_text = await _fetch_text(session, requirements_url)
            except Exception:
                requirements_text = ""
    return {
        "kind": "folder",
        "module_name": folder_name,
        "entry_filename": entry_name,
        "source": source,
        "source_url": normalize_github_raw_url(raw_url),
        "link": _github_tree_url(owner, repo, ref, path),
        "image_url": str(image_item.get("download_url") or "") if image_item else "",
        "image_name": str(image_item.get("name") or "Module.png") if image_item else "",
        "requirements_text": requirements_text,
        "requirements": parse_requirements_text(requirements_text),
        "requirements_url": requirements_url,
        "trusted": trusted_repo_link(_github_tree_url(owner, repo, ref, path)),
        "repo_owner": owner,
        "repo_name": repo,
        "repo_ref": ref,
        "repo_path": path,
    }


async def fetch_module_bundle(link: str) -> dict:
    value = _normalize_url(link)
    if not value:
        raise RuntimeError("Provide a module link")
    parsed = _parse_github_link(value)
    try:
        async with aiohttp.ClientSession() as session:
            if parsed and parsed["kind"] == "tree":
                return await _fetch_folder_bundle(
                    session,
                    parsed["owner"],
                    parsed["repo"],
                    parsed["ref"],
                    parsed["path"],
                )
            if parsed and parsed["kind"] == "contents" and parsed["path"]:
                payload = await _fetch_json(session, parsed["url"])
                if isinstance(payload, list):
                    return await _fetch_folder_bundle(
                        session,
                        parsed["owner"],
                        parsed["repo"],
                        parsed["ref"],
                        parsed["path"],
                    )
            url = normalize_github_raw_url(value)
            filename = Path(urlparse(url).path).name or "module.py"
            if not filename.endswith(".py"):
                raise RuntimeError("URL must point to a .py module or a GitHub module folder")
            source = await _fetch_text(session, url)
    except aiohttp.InvalidURL as exc:
        raise RuntimeError("Invalid module URL") from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError(f"Module download failed: {exc}") from exc
    return {
        "kind": "file",
        "module_name": Path(filename).stem,
        "entry_filename": filename,
        "source": source,
        "source_url": url,
        "link": value,
        "image_url": "",
        "image_name": "",
        "requirements_text": "",
        "requirements": [],
        "requirements_url": "",
        "trusted": trusted_repo_link(value),
        "repo_owner": "",
        "repo_name": "",
        "repo_ref": "",
        "repo_path": "",
    }


def _normalize_repo_item(item: dict) -> dict:
    install_link = str(
        item.get("link")
        or item.get("install_link")
        or item.get("html_url")
        or item.get("raw")
        or item.get("url")
        or item.get("download_url")
        or ""
    )
    raw_link = normalize_github_raw_url(str(item.get("raw_link") or item.get("raw") or item.get("download_url") or install_link))
    install_link = _normalize_url(install_link)
    if not install_link and raw_link:
        install_link = _derive_tree_link_from_raw(raw_link) or raw_link
    elif install_link == raw_link:
        install_link = _derive_tree_link_from_raw(raw_link) or install_link
    image = str(item.get("image") or item.get("Image") or item.get("Module.png") or item.get("Image.png") or "")
    name = str(item.get("name") or Path(raw_link.split("?", 1)[0]).stem or "module")
    description = str(item.get("description") or f"{name} module from DTG_Modules")
    return {
        **item,
        "name": name,
        "description": description,
        "image": image,
        "modul_png": image,
        "link": install_link or raw_link,
        "raw_link": raw_link,
        "author": str(item.get("author") or "DTG"),
        "version": str(item.get("version") or "latest"),
        "verified": trusted_repo_link(install_link or raw_link),
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
            py_item = _pick_python_file(sub_items, name)
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
                        "link": item.get("html_url") or "",
                        "raw_link": py_item.get("download_url") or py_item.get("html_url") or "",
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
                        "raw_link": item.get("download_url") or item.get("html_url") or "",
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
        raw_link = str(item.get("raw_link") or "").strip().lower()
        if name == needle or Path(raw_link.split("?", 1)[0]).stem.lower() == needle or Path(link.split("?", 1)[0]).stem.lower() == needle:
            return item
    return None
