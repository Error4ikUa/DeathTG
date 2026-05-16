from __future__ import annotations

import re
from pathlib import Path

from deathtg.config import MODULES_DIR, ROOT_DIR


IMAGES_DIR = ROOT_DIR / "images"
MODULE_IMAGES_DIR = IMAGES_DIR / "modules"
MODULE_CARD_NAME = "Module.png"
SYSTEM_IMAGE_FILES = {
    "welcome": "welcome_deathtg.png",
    "update_available": "update_available_deathtg.png",
    "creating_backup": "creating_backup.png",
}


def system_image(name: str) -> Path | None:
    filename = SYSTEM_IMAGE_FILES.get(name, "")
    if not filename:
        return None
    path = IMAGES_DIR / filename
    return path if path.exists() else None


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
    for stem in _asset_name_candidates(module_name):
        for suffix in (".png", ".jpg", ".jpeg", ".webp"):
            candidate = MODULE_IMAGES_DIR / f"{stem}{suffix}"
            if candidate.exists():
                return candidate
    fallback = MODULE_IMAGES_DIR / MODULE_CARD_NAME
    return fallback if fallback.exists() else None


def module_image_path(module_name: str) -> Path | None:
    return local_module_image_path(module_name) or shared_module_image_path(module_name)
