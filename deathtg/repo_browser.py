from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from deathtg.config import MODULES_DIR
from deathtg.module_repo import fetch_repo_modules, trusted_repo_link
from deathtg.state_db import set_health, upsert


@dataclass(slots=True)
class RepoModuleCard:
    module_key: str
    name: str
    description: str
    author: str
    version: str
    link: str
    raw_link: str
    image: str
    installed: bool
    trusted: bool
    status: str
    tags: list[str]


def _installed_keys() -> set[str]:
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    keys: set[str] = set()
    for path in MODULES_DIR.iterdir():
        if path.name.startswith("_"):
            continue
        if path.is_dir():
            keys.add(path.name.lower())
        elif path.suffix.lower() == ".py":
            keys.add(path.stem.lower())
    return keys


def _module_key(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    if name:
        return Path(name).name
    raw = str(item.get("raw_link") or item.get("link") or "")
    return Path(raw.split("?", 1)[0]).stem or "module"


def _normalize_card(item: dict[str, Any], installed: set[str]) -> RepoModuleCard:
    key = _module_key(item)
    link = str(item.get("link") or "")
    raw_link = str(item.get("raw_link") or "")
    trusted = bool(item.get("verified") or trusted_repo_link(link) or trusted_repo_link(raw_link))
    is_installed = key.lower() in installed
    tags: list[str] = []
    if is_installed:
        tags.append("installed")
    if trusted:
        tags.append("trusted")
    if str(item.get("image") or ""):
        tags.append("image")
    return RepoModuleCard(
        module_key=key,
        name=str(item.get("name") or key),
        description=str(item.get("description") or ""),
        author=str(item.get("author") or "DTG"),
        version=str(item.get("version") or "latest"),
        link=link,
        raw_link=raw_link,
        image=str(item.get("image") or item.get("modul_png") or ""),
        installed=is_installed,
        trusted=trusted,
        status="installed" if is_installed else "available",
        tags=tags,
    )


def sync_repo_cards(cards: list[RepoModuleCard]) -> None:
    for card in cards:
        upsert(
            "module_sources",
            "source_id",
            f"repo.{card.module_key}",
            {
                "module_key": card.module_key,
                "source_type": "repo_browser",
                "url": card.link or card.raw_link,
                "path": card.image,
                "trusted": 1 if card.trusted else 0,
            },
            preserve_existing=False,
            event_type="repo_browser.sync",
        )
        if card.installed:
            upsert(
                "modules",
                "module_key",
                card.module_key,
                {
                    "name": card.name,
                    "source_url": card.link or card.raw_link,
                    "version": card.version,
                    "author": card.author,
                },
                preserve_existing=True,
                event_type="repo_browser.installed_hint",
            )


def filter_cards(cards: list[RepoModuleCard], *, query: str = "", status: str = "all", trusted: bool | None = None) -> list[RepoModuleCard]:
    needle = query.strip().lower()
    result: list[RepoModuleCard] = []
    for card in cards:
        if needle and needle not in " ".join([card.name, card.description, card.author, card.module_key]).lower():
            continue
        if status == "installed" and not card.installed:
            continue
        if status == "available" and card.installed:
            continue
        if trusted is not None and card.trusted != trusted:
            continue
        result.append(card)
    return result


async def fetch_repo_browser_cards() -> list[RepoModuleCard]:
    installed = _installed_keys()
    items = await fetch_repo_modules()
    cards = [_normalize_card(dict(item), installed) for item in items]
    sync_repo_cards(cards)
    set_health(
        "repo_browser",
        "ok" if cards else "warning",
        f"Repo browser indexed {len(cards)} module(s)" if cards else "Repo browser returned no modules",
        {"modules": [asdict(card) for card in cards]},
    )
    return cards


def fetch_repo_browser_cards_sync() -> list[RepoModuleCard]:
    return asyncio.run(fetch_repo_browser_cards())
