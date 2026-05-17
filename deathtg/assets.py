from __future__ import annotations

import re
from pathlib import Path

from deathtg.config import MODULES_DIR, ROOT_DIR


IMAGE_DIR_CANDIDATES = [ROOT_DIR / "images", ROOT_DIR / "Image"]
IMAGES_DIR = next((path for path in IMAGE_DIR_CANDIDATES if path.exists()), IMAGE_DIR_CANDIDATES[0])
MODULE_IMAGES_DIR = IMAGES_DIR / "modules"
MODULE_CARD_NAME = "Module.png"
SYSTEM_IMAGE_FILES = {
    "welcome": "DeathTG_welcome.png",
    "update_available": "DeathTG_update_available.png",
    "creating_backup": "DeathTG_creating_backup.png",
}
DEFAULT_AVATAR_CANDIDATES = [
    ROOT_DIR / "deathtg" / "panel" / "static" / "default_avatar.png",
    ROOT_DIR / "images" / "DeathTG_Avatarka.png",
    ROOT_DIR / "Image" / "DeathTG_Avatarka.png",
]


def system_image(name: str) -> Path | None:
    filename = SYSTEM_IMAGE_FILES.get(name, "")
    if not filename:
        return None
    fallbacks = {
        "DeathTG_welcome.png": ["welcome_deathtg.png"],
        "DeathTG_update_available.png": ["update_available_deathtg.png"],
        "DeathTG_creating_backup.png": ["creating_backup.png"],
    }
    for directory in IMAGE_DIR_CANDIDATES:
        for candidate in [filename, *fallbacks.get(filename, [])]:
            path = directory / candidate
            if path.exists():
                return path
    return None


def module_entry_candidates(module_dir: Path, module_name: str | None = None) -> list[Path]:
    name = (module_name or module_dir.name).strip() or module_dir.name
    return [
        module_dir / f"{name}.py",
        module_dir / "main.py",
        module_dir / "__init__.py",
    ]


def resolve_module_entry(path: Path, module_name: str | None = None) -> Path | None:
    if path.is_file() and path.suffix.lower() == ".py":
        return path
    if path.is_dir():
        for candidate in module_entry_candidates(path, module_name):
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def installed_module_root(module_name: str) -> Path | None:
    folder = MODULES_DIR / module_name
    if folder.is_dir():
        return folder
    file_path = MODULES_DIR / f"{module_name}.py"
    if file_path.exists():
        return file_path
    return None


def installed_module_source(module_name: str) -> Path | None:
    root = installed_module_root(module_name)
    if root is None:
        return None
    return resolve_module_entry(root, module_name)


def local_module_image_path(module_name: str) -> Path | None:
    folder = MODULES_DIR / module_name
    if folder.is_dir():
        for candidate in (folder / MODULE_CARD_NAME, folder / MODULE_CARD_NAME.lower()):
            if candidate.exists():
                return candidate
    return None


def _asset_name_candidates(module_name: str) -> list[str]:
    base = (module_name or "").strip()
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")
    lowered = normalized.lower()
    compact = lowered.replace("_", "").replace("-", "")
    ordered = [base, normalized, lowered, compact]
    seen: list[str] = []
    for item in ordered:
        if item and item not in seen:
            seen.append(item)
    return seen


def shared_module_image_path(module_name: str) -> Path | None:
    for directory in IMAGE_DIR_CANDIDATES:
        module_dir = directory / "modules"
        for stem in _asset_name_candidates(module_name):
            for suffix in (".png", ".jpg", ".jpeg", ".webp"):
                candidate = module_dir / f"{stem}{suffix}"
                if candidate.exists():
                    return candidate
        fallback = module_dir / MODULE_CARD_NAME
        if fallback.exists():
            return fallback
    return None


def module_image_path(module_name: str) -> Path | None:
    return local_module_image_path(module_name) or shared_module_image_path(module_name)


def default_avatar_path() -> Path | None:
    for path in DEFAULT_AVATAR_CANDIDATES:
        if path.exists():
            return path
    return None
