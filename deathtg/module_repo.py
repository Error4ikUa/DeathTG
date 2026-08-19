from __future__ import annotations

import asyncio
import os
import re
import hashlib
import io
import ipaddress
import json
import socket
import time
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import aiohttp

from deathtg.config import RUNTIME_DIR


MODULE_REPO_INDEX = os.getenv(
    "MODULE_REPO_INDEX",
    "https://raw.githubusercontent.com/Error4ikUa/DTG_Modules/main/index.json",
)
MODULE_REPO_API = os.getenv(
    "MODULE_REPO_API",
    "https://api.github.com/repos/Error4ikUa/DTG_Modules/contents?ref=main",
)
MODULE_REPO_ZIP = os.getenv(
    "MODULE_REPO_ZIP",
    "https://codeload.github.com/Error4ikUa/DTG_Modules/zip/refs/heads/main",
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
GITHUB_TREE_LINK_RE = re.compile(
    r'/((?P<owner>[^/]+)/(?P<repo>[^/]+)/tree/(?P<ref>[^/]+)/(?P<path>[^"#?<> ]+))',
    re.I,
)
GITHUB_BLOB_LINK_RE = re.compile(
    r'/((?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<ref>[^/]+)/(?P<path>[^"#?<> ]+))',
    re.I,
)


GITHUB_HEADERS = {
    "User-Agent": "DeathTG/1.0",
    "Accept": "application/vnd.github+json, text/html;q=0.9, */*;q=0.8",
}

MODULE_REPO_CACHE_PATH = RUNTIME_DIR / "module_repo_cache.json"
MODULE_BUNDLE_CACHE_DIR = RUNTIME_DIR / "module_bundle_cache"
MAX_BUNDLE_CACHE_BYTES = 1024 * 1024 * 5
MODULE_REPO_CACHE_TTL_SECONDS = 5 * 60
MAX_MODULE_SOURCE_BYTES = 2 * 1024 * 1024
MAX_REQUIREMENTS_BYTES = 256 * 1024
MAX_REPO_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REPO_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_REPO_ARCHIVE_MEMBERS = 5_000
MAX_REPO_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_REPO_MEMBER_BYTES = 16 * 1024 * 1024
MAX_REPO_COMPRESSION_RATIO = 500
MAX_REMOTE_IMAGE_BYTES = 10 * 1024 * 1024
SAFE_DOWNLOAD_HOSTS = {
    "api.github.com",
    "codeload.github.com",
    "github.com",
    "gitlab.com",
    "raw.githubusercontent.com",
}


def _json_load(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _repo_cache_items() -> list[dict]:
    data = _json_load(MODULE_REPO_CACHE_PATH, {})
    if not isinstance(data, dict):
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _repo_cache_age() -> int | None:
    data = _json_load(MODULE_REPO_CACHE_PATH, {})
    if not isinstance(data, dict):
        return None
    try:
        updated_at = int(data.get("updated_at", 0) or 0)
    except (TypeError, ValueError):
        return None
    return max(0, int(time.time()) - updated_at) if updated_at else None


def _write_repo_cache(items: list[dict], *, source: str = "remote") -> None:
    if not items:
        return
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    MODULE_REPO_CACHE_PATH.write_text(
        json.dumps(
            {
                "source": source,
                "updated_at": int(time.time()),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _bundle_cache_path(link: str) -> Path:
    digest = hashlib.sha256(_normalize_url(link).encode("utf-8")).hexdigest()
    return MODULE_BUNDLE_CACHE_DIR / f"{digest}.json"


def _read_bundle_cache(link: str) -> dict | None:
    data = _json_load(_bundle_cache_path(link), {})
    if not isinstance(data, dict):
        return None
    bundle = data.get("bundle")
    return dict(bundle) if isinstance(bundle, dict) and bundle.get("source") else None


def _write_bundle_cache(link: str, bundle: dict) -> None:
    source = str(bundle.get("source") or "")
    if not source or len(source.encode("utf-8", errors="ignore")) > MAX_BUNDLE_CACHE_BYTES:
        return
    MODULE_BUNDLE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _bundle_cache_path(link).write_text(
        json.dumps(
            {
                "link": _normalize_url(link),
                "updated_at": int(time.time()),
                "bundle": bundle,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _normalize_url(value: str) -> str:
    value = (value or "").strip().strip("'\"")
    if value.startswith("www."):
        return "https://" + value
    if value.startswith("github.com/") or value.startswith("raw.githubusercontent.com/") or value.startswith("api.github.com/"):
        return "https://" + value
    return value


async def _assert_public_download_url(value: str) -> str:
    url = _normalize_url(value)
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError("Module downloads require a public HTTPS URL")
    if parsed.username or parsed.password:
        raise RuntimeError("Module URL must not contain credentials")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise RuntimeError("Module URL points to a local network host")
    if hostname in SAFE_DOWNLOAD_HOSTS or hostname.endswith(".githubusercontent.com"):
        return url
    try:
        direct_ip = ipaddress.ip_address(hostname)
        addresses = [direct_ip]
    except ValueError:
        try:
            resolved = await asyncio.get_running_loop().getaddrinfo(
                hostname,
                parsed.port or 443,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise RuntimeError("Module host could not be resolved") from exc
        addresses = []
        for item in resolved:
            try:
                addresses.append(ipaddress.ip_address(item[4][0]))
            except (IndexError, ValueError):
                continue
    if not addresses or any(not address.is_global for address in addresses):
        raise RuntimeError("Module URL resolves to a private or reserved network")
    return url


@asynccontextmanager
async def _safe_get(session: aiohttp.ClientSession, url: str, *, timeout: int, headers: dict | None = None):
    current = url
    for _ in range(4):
        current = await _assert_public_download_url(current)
        response = await session.get(
            current,
            timeout=timeout,
            headers=headers,
            allow_redirects=False,
        )
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("location", "").strip()
            response.release()
            if not location:
                raise RuntimeError("Module download redirect has no destination")
            current = urljoin(current, location)
            continue
        try:
            yield response
        finally:
            response.release()
        return
    raise RuntimeError("Module download has too many redirects")


async def read_limited_response(response: aiohttp.ClientResponse, max_bytes: int, label: str) -> bytes:
    try:
        declared = int(response.headers.get("content-length", "0") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > max_bytes:
        raise RuntimeError(f"{label} is too large")
    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise RuntimeError(f"{label} is too large")
    return bytes(payload)


async def fetch_public_binary(url: str, *, max_bytes: int, label: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with _safe_get(session, url, timeout=20, headers=GITHUB_HEADERS) as response:
            if response.status != 200:
                raise RuntimeError(f"{label} download failed, HTTP {response.status}")
            return await read_limited_response(response, max_bytes, label)


def _validated_archive_infos(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_REPO_ARCHIVE_MEMBERS:
        raise RuntimeError("Repository archive contains too many files")
    total = 0
    result: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise RuntimeError("Encrypted repository archives are not supported")
        if info.file_size > MAX_REPO_MEMBER_BYTES:
            raise RuntimeError(f"Repository file is too large: {info.filename}")
        total += info.file_size
        if total > MAX_REPO_EXPANDED_BYTES:
            raise RuntimeError("Repository archive expands beyond the allowed size")
        if info.file_size and (
            info.compress_size <= 0
            or info.file_size / max(1, info.compress_size) > MAX_REPO_COMPRESSION_RATIO
        ):
            raise RuntimeError(f"Suspicious compression ratio in repository file: {info.filename}")
        result[info.filename] = info
    return result


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


def _github_raw_url(owner: str, repo: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path.strip('/')}"


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
    async with _safe_get(session, url, timeout=20, headers=GITHUB_HEADERS) as response:
        if response.status != 200:
            raise RuntimeError(f"Download failed, HTTP {response.status}")
        return (await read_limited_response(response, MAX_MODULE_SOURCE_BYTES, "Module source")).decode("utf-8", errors="replace")


async def _fetch_json(session: aiohttp.ClientSession, url: str):
    async with _safe_get(session, url, timeout=20, headers=GITHUB_HEADERS) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub API failed, HTTP {response.status}")
        payload = await read_limited_response(response, MAX_REPO_RESPONSE_BYTES, "Repository metadata")
        return json.loads(payload.decode("utf-8", errors="replace"))


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    async with _safe_get(session, url, timeout=20, headers=GITHUB_HEADERS) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub page failed, HTTP {response.status}")
        return (await read_limited_response(response, MAX_REPO_RESPONSE_BYTES, "Repository page")).decode("utf-8", errors="replace")


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


def _pick_zip_entry(entries: list[str], folder_name: str) -> str:
    preferred = [
        f"{folder_name}/{folder_name}.py",
        f"{folder_name}/main.py",
        f"{folder_name}/__init__.py",
    ]
    entry_set = set(entries)
    for candidate in preferred:
        if candidate in entry_set:
            return candidate
    for entry in entries:
        name = PurePosixPath(entry).name
        if entry.lower().endswith(".py") and not name.startswith("_"):
            return entry
    return ""


def _zip_module_items(data: bytes, owner: str, repo: str, ref: str) -> list[dict]:
    modules: list[dict] = []
    if len(data) > MAX_REPO_ARCHIVE_BYTES:
        raise RuntimeError("Repository archive is too large")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = _validated_archive_infos(archive)
            names = list(infos)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Repository returned an invalid ZIP archive") from exc
    if not names:
        return modules
    relative = [name.split("/", 1)[1] for name in names if "/" in name and name.split("/", 1)[1]]
    by_folder: dict[str, list[str]] = {}
    top_level_py: list[str] = []
    for item in relative:
        parts = item.split("/", 1)
        if len(parts) == 1:
            if item.lower().endswith(".py") and not item.startswith("_"):
                top_level_py.append(item)
            continue
        folder = parts[0].strip()
        if not folder or folder.startswith(".") or folder.startswith("_"):
            continue
        by_folder.setdefault(folder, []).append(item)

    for folder, entries in sorted(by_folder.items(), key=lambda row: row[0].lower()):
        py_entry = _pick_zip_entry(entries, folder)
        if not py_entry:
            continue
        image_entry = next(
            (
                entry
                for entry in entries
                if PurePosixPath(entry).name.lower() in {"module.png", "image.png"}
                and str(PurePosixPath(entry).parent).strip("/") == folder
            ),
            "",
        )
        modules.append(
            _normalize_repo_item(
                {
                    "name": folder,
                    "description": f"{folder} module from DTG_Modules",
                    "image": _github_raw_url(owner, repo, ref, image_entry) if image_entry else "",
                    "link": _github_tree_url(owner, repo, ref, folder),
                    "raw_link": _github_raw_url(owner, repo, ref, py_entry),
                }
            )
        )

    for path in sorted(top_level_py, key=str.lower):
        name = PurePosixPath(path).stem
        modules.append(
            _normalize_repo_item(
                {
                    "name": name,
                    "description": f"{name} module from DTG_Modules",
                    "link": _github_raw_url(owner, repo, ref, path),
                    "raw_link": _github_raw_url(owner, repo, ref, path),
                }
            )
        )
    return modules


async def _from_github_zip_archive(
    session: aiohttp.ClientSession,
    owner: str = "Error4ikUa",
    repo: str = "DTG_Modules",
    ref: str = "main",
) -> list[dict]:
    url = MODULE_REPO_ZIP or f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
    async with _safe_get(session, url, timeout=30, headers=GITHUB_HEADERS) as response:
        if response.status != 200:
            return []
        payload = await read_limited_response(response, MAX_REPO_ARCHIVE_BYTES, "Repository archive")
    return _zip_module_items(payload, owner, repo, ref)


async def _fetch_folder_bundle_from_zip(
    session: aiohttp.ClientSession,
    owner: str,
    repo: str,
    ref: str,
    path: str,
) -> dict:
    url = MODULE_REPO_ZIP or f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
    async with _safe_get(session, url, timeout=30, headers=GITHUB_HEADERS) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub archive failed, HTTP {response.status}")
        payload = await read_limited_response(response, MAX_REPO_ARCHIVE_BYTES, "Repository archive")
    clean_path = path.strip("/")
    folder_name = PurePosixPath(clean_path).name or "module"
    prefix = clean_path + "/"
    try:
        archive_context = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Repository returned an invalid ZIP archive") from exc
    with archive_context as archive:
        infos = _validated_archive_infos(archive)
        names = list(infos)
        relative = [name.split("/", 1)[1] for name in names if "/" in name and name.split("/", 1)[1]]
        entries = [entry for entry in relative if entry.startswith(prefix)]
        preferred = [
            f"{clean_path}/{folder_name}.py",
            f"{clean_path}/main.py",
            f"{clean_path}/__init__.py",
        ]
        py_entry = next((candidate for candidate in preferred if candidate in entries), "")
        if not py_entry:
            py_entry = next(
                (
                    entry
                    for entry in entries
                    if entry.lower().endswith(".py")
                    and not PurePosixPath(entry).name.startswith("_")
                ),
                "",
            )
        if not py_entry:
            raise RuntimeError("Folder does not contain a Python module entry")
        zip_py_entry = next(name for name in names if name.endswith("/" + py_entry))
        if infos[zip_py_entry].file_size > MAX_MODULE_SOURCE_BYTES:
            raise RuntimeError("Module source is too large")
        source = archive.read(zip_py_entry).decode("utf-8", errors="replace")
        image_entry = next(
            (
                entry
                for entry in entries
                if PurePosixPath(entry).name.lower() in {"module.png", "image.png"}
                and str(PurePosixPath(entry).parent).strip("/") == clean_path
            ),
            "",
        )
        requirements_entry = next(
            (
                entry
                for entry in entries
                if PurePosixPath(entry).name.lower() == "requirements.txt"
                and str(PurePosixPath(entry).parent).strip("/") == clean_path
            ),
            "",
        )
        requirements_text = ""
        if requirements_entry:
            zip_req_entry = next(name for name in names if name.endswith("/" + requirements_entry))
            if infos[zip_req_entry].file_size > MAX_REQUIREMENTS_BYTES:
                raise RuntimeError("Module requirements file is too large")
            requirements_text = archive.read(zip_req_entry).decode("utf-8", errors="replace")

    return {
        "kind": "folder",
        "module_name": folder_name,
        "entry_filename": PurePosixPath(py_entry).name,
        "source": source,
        "source_url": _github_raw_url(owner, repo, ref, py_entry),
        "link": _github_tree_url(owner, repo, ref, clean_path),
        "image_url": _github_raw_url(owner, repo, ref, image_entry) if image_entry else "",
        "image_name": PurePosixPath(image_entry).name if image_entry else "",
        "requirements_text": requirements_text,
        "requirements": parse_requirements_text(requirements_text),
        "requirements_url": _github_raw_url(owner, repo, ref, requirements_entry) if requirements_entry else "",
        "trusted": trusted_repo_link(_github_tree_url(owner, repo, ref, clean_path)),
        "repo_owner": owner,
        "repo_name": repo,
        "repo_ref": ref,
        "repo_path": clean_path,
    }


async def _fetch_folder_bundle(session: aiohttp.ClientSession, owner: str, repo: str, ref: str, path: str) -> dict:
    listing_url = _github_contents_url(owner, repo, path, ref)
    folder_name = PurePosixPath(path).name or "module"
    entry_name = f"{folder_name}.py"
    source = ""
    image_url = ""
    image_name = ""
    requirements_text = ""
    requirements_url = ""
    try:
        listing = await _fetch_json(session, listing_url)
        if not isinstance(listing, list):
            raise RuntimeError("Folder link did not return a module directory")
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
        if image_item:
            image_url = str(image_item.get("download_url") or "")
            image_name = str(image_item.get("name") or "Module.png")
        if requirements_item:
            requirements_url = str(requirements_item.get("download_url") or "")
            if requirements_url:
                try:
                    requirements_text = await _fetch_text(session, requirements_url)
                except Exception:
                    requirements_text = ""
    except Exception:
        try:
            html_url = _github_tree_url(owner, repo, ref, path)
            html = await _fetch_html(session, html_url)
            blob_paths = sorted(
                {
                    match.group("path")
                    for match in GITHUB_BLOB_LINK_RE.finditer(html)
                    if str(match.group("owner")).lower() == owner.lower()
                    and str(match.group("repo")).lower() == repo.lower()
                    and str(match.group("ref")) == ref
                    and str(match.group("path")).startswith(path.strip("/") + "/")
                }
            )
            py_candidates = [blob for blob in blob_paths if blob.lower().endswith(".py")]
            preferred = [
                f"{path.strip('/')}/{folder_name}.py",
                f"{path.strip('/')}/main.py",
                f"{path.strip('/')}/__init__.py",
            ]
            chosen = next((candidate for candidate in preferred if candidate in py_candidates), None)
            if not chosen and py_candidates:
                chosen = next((candidate for candidate in py_candidates if not PurePosixPath(candidate).name.startswith("_")), py_candidates[0])
            if not chosen:
                raise RuntimeError("Folder does not contain a Python module entry")
            entry_name = PurePosixPath(chosen).name
            source = await _fetch_text(session, _github_raw_url(owner, repo, ref, chosen))
            image_candidates = [blob for blob in blob_paths if PurePosixPath(blob).name.lower() in {"module.png", "image.png"}]
            if image_candidates:
                image_name = PurePosixPath(image_candidates[0]).name
                image_url = _github_raw_url(owner, repo, ref, image_candidates[0])
            requirements_candidates = [blob for blob in blob_paths if PurePosixPath(blob).name.lower() == "requirements.txt"]
            if requirements_candidates:
                requirements_url = _github_raw_url(owner, repo, ref, requirements_candidates[0])
                try:
                    requirements_text = await _fetch_text(session, requirements_url)
                except Exception:
                    requirements_text = ""
        except Exception:
            return await _fetch_folder_bundle_from_zip(session, owner, repo, ref, path)
    return {
        "kind": "folder",
        "module_name": folder_name,
        "entry_filename": entry_name,
        "source": source,
        "source_url": _github_raw_url(owner, repo, ref, f"{path.strip('/')}/{entry_name}"),
        "link": _github_tree_url(owner, repo, ref, path),
        "image_url": image_url,
        "image_name": image_name,
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
                bundle = await _fetch_folder_bundle(
                    session,
                    parsed["owner"],
                    parsed["repo"],
                    parsed["ref"],
                    parsed["path"],
                )
                _write_bundle_cache(value, bundle)
                return bundle
            if parsed and parsed["kind"] == "contents" and parsed["path"]:
                bundle = await _fetch_folder_bundle(
                    session,
                    parsed["owner"],
                    parsed["repo"],
                    parsed["ref"],
                    parsed["path"],
                )
                _write_bundle_cache(value, bundle)
                return bundle
            url = normalize_github_raw_url(value)
            filename = Path(urlparse(url).path).name or "module.py"
            if not filename.endswith(".py"):
                raise RuntimeError("URL must point to a .py module or a GitHub module folder")
            source = await _fetch_text(session, url)
    except aiohttp.InvalidURL as exc:
        raise RuntimeError("Invalid module URL") from exc
    except aiohttp.ClientError as exc:
        cached = _read_bundle_cache(value)
        if cached:
            return cached
        raise RuntimeError(f"Module download failed: {exc}") from exc
    except RuntimeError:
        cached = _read_bundle_cache(value)
        if cached:
            return cached
        raise
    bundle = {
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
    _write_bundle_cache(value, bundle)
    return bundle


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
    async with _safe_get(session, MODULE_REPO_INDEX, timeout=12) as response:
        if response.status != 200:
            return []
        payload = await read_limited_response(response, MAX_REPO_RESPONSE_BYTES, "Module index")
        data = json.loads(payload.decode("utf-8", errors="replace"))
    items = data.get("modules", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [_normalize_repo_item(dict(item)) for item in items if isinstance(item, dict)]


async def _from_github_contents(session: aiohttp.ClientSession) -> list[dict]:
    async with _safe_get(session, MODULE_REPO_API, timeout=12, headers=GITHUB_HEADERS) as response:
        if response.status != 200:
            return []
        payload = await read_limited_response(response, MAX_REPO_RESPONSE_BYTES, "Repository listing")
        data = json.loads(payload.decode("utf-8", errors="replace"))
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
            async with _safe_get(session, dir_url, timeout=12, headers=GITHUB_HEADERS) as sub_response:
                if sub_response.status != 200:
                    continue
                payload = await read_limited_response(sub_response, MAX_REPO_RESPONSE_BYTES, "Repository directory")
                sub_items = json.loads(payload.decode("utf-8", errors="replace"))
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


async def _from_github_tree_html(session: aiohttp.ClientSession, owner: str = "Error4ikUa", repo: str = "DTG_Modules", ref: str = "main") -> list[dict]:
    html = await _fetch_html(session, _github_tree_url(owner, repo, ref, ""))
    tree_paths = sorted(
        {
            match.group("path")
            for match in GITHUB_TREE_LINK_RE.finditer(html)
            if str(match.group("owner")).lower() == owner.lower()
            and str(match.group("repo")).lower() == repo.lower()
            and str(match.group("ref")) == ref
            and "/" not in str(match.group("path"))
        }
    )
    blob_paths = sorted(
        {
            match.group("path")
            for match in GITHUB_BLOB_LINK_RE.finditer(html)
            if str(match.group("owner")).lower() == owner.lower()
            and str(match.group("repo")).lower() == repo.lower()
            and str(match.group("ref")) == ref
            and "/" not in str(match.group("path"))
            and str(match.group("path")).lower().endswith(".py")
        }
    )
    modules: list[dict] = []
    for path in tree_paths:
        name = PurePosixPath(path).name
        modules.append(
            _normalize_repo_item(
                {
                    "name": name,
                    "description": f"{name} module from DTG_Modules",
                    "link": _github_tree_url(owner, repo, ref, path),
                    "raw_link": _github_raw_url(owner, repo, ref, f"{path}/{name}.py"),
                }
            )
        )
    for path in blob_paths:
        name = PurePosixPath(path).stem
        modules.append(
            _normalize_repo_item(
                {
                    "name": name,
                    "description": f"{name} module from DTG_Modules",
                    "link": _github_raw_url(owner, repo, ref, path),
                    "raw_link": _github_raw_url(owner, repo, ref, path),
                }
            )
        )
    return modules


def _dedupe_repo_items(items: list[dict]) -> list[dict]:
    unique: dict[str, dict] = {}
    for item in items:
        key = str(item.get("name") or "").strip().lower()
        if not key:
            continue
        if key not in unique:
            unique[key] = dict(item)
            continue
        # The index is intentionally processed first so its curated metadata
        # wins. Archive discovery then fills missing assets such as Module.png
        # without replacing descriptions, authors or explicit install links.
        current = unique[key]
        for field, value in item.items():
            if not current.get(field) and value not in (None, "", [], {}):
                current[field] = value
        image = str(current.get("image") or current.get("modul_png") or "")
        current["image"] = image
        current["modul_png"] = image
    return sorted(unique.values(), key=lambda item: str(item.get("name") or "").lower())


async def fetch_repo_modules(*, refresh: bool = False) -> list[dict]:
    cached = _dedupe_repo_items(_repo_cache_items())
    cache_age = _repo_cache_age()
    if not refresh and cached and cache_age is not None and cache_age <= MODULE_REPO_CACHE_TTL_SECONDS:
        return cached
    try:
        async with aiohttp.ClientSession() as session:
            index_result, archive_result = await asyncio.gather(
                _from_index(session),
                _from_github_zip_archive(session),
                return_exceptions=True,
            )
            index_items = index_result if isinstance(index_result, list) else []
            archive_items = archive_result if isinstance(archive_result, list) else []
            items = [*index_items, *archive_items]
            if not items:
                items = await _from_github_contents(session)
            if not items:
                items = await _from_github_tree_html(session)
    except Exception:
        return cached
    normalized = _dedupe_repo_items(items)
    if not normalized:
        return cached
    # GitHub may return a partial directory listing while rate limited. Never
    # replace a known-good catalog with a smaller transient response.
    if cached and not archive_items and len(normalized) < len(cached):
        normalized = _dedupe_repo_items([*normalized, *cached])
    _write_repo_cache(normalized)
    return normalized


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
