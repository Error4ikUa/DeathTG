from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import re
import string
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import UnblockRequest
from telethon.tl.functions.folders import EditPeerFoldersRequest
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.tl.types import DialogFilter, InputFolderPeer, TextWithEntities

from deathtg.config import ENV_PATH, ROOT_DIR, RUNTIME_DIR
from deathtg.assets import default_avatar_path, system_image
from deathtg.community_roles import (
    community_bot_display_name,
    community_enabled_for_owner,
    preferred_community_bot_username,
)
from deathtg.panel_access import issue_device_grant, panel_remote_access_ready
from deathtg.profile_store import profile_settings, update_env_value


TARGET_CHANNELS = ("Death_Telega", "Death_TgOfftop")
FOLDER_NAME = "DeathTG"
STATUS_PATH = RUNTIME_DIR / "startup_status.json"
BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
BOTFATHER_RETRY_RE = re.compile(r"too many attempts.*?try again in\s+(\d+)\s+seconds?", re.IGNORECASE)
BOT_AVATAR = default_avatar_path() or (ROOT_DIR / "deathtg" / "panel" / "static" / "default_avatar.png")


def _env(name: str) -> str:
    load_dotenv(ENV_PATH, override=True)
    return os.getenv(name, "").strip()


def _write_status(payload: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_status() -> dict:
    if not STATUS_PATH.exists():
        return {}
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _issue_panel_grant(owner_id: int, ttl_seconds: int = 60 * 60 * 24 * 7) -> str:
    device_label = f"Telegram shortcut {owner_id}"
    return issue_device_grant(device_label, ttl_seconds=ttl_seconds, created_by="startup_sync", owner_id=owner_id)


def _build_panel_grant_url(owner_id: int) -> str:
    return _issue_panel_grant(owner_id)


def _shortcuts_interval_seconds() -> int:
    raw = _env("PANEL_SHORTCUTS_MIN_INTERVAL")
    if not raw:
        return 60 * 60 * 6
    try:
        value = int(raw)
    except Exception:
        return 60 * 60 * 6
    return max(0, min(value, 60 * 60 * 24 * 30))


def _shortcuts_allowed_now() -> tuple[bool, str | None, int]:
    interval = _shortcuts_interval_seconds()
    if interval <= 0:
        return True, None, interval
    previous = _load_status()
    shortcuts = previous.get("shortcuts", {}) if isinstance(previous, dict) else {}
    if not isinstance(shortcuts, dict):
        shortcuts = {}
    last_sent_at = int(shortcuts.get("sent_at", 0) or 0)
    now = int(time.time())
    if not last_sent_at or now - last_sent_at >= interval:
        return True, None, interval
    wait_left = interval - (now - last_sent_at)
    return False, f"cooldown active ({wait_left}s left)", interval


def _expected_prefix(owner_id: int) -> str:
    return f"dtg{owner_id}_"


def _bot_username_re(owner_id: int) -> re.Pattern[str]:
    return re.compile(rf"^dtg{owner_id}_[a-z0-9]{{4,16}}_bot$", re.IGNORECASE)


def _is_valid_bot_username(username: str, owner_id: int) -> bool:
    return bool(username and _bot_username_re(owner_id).fullmatch(username))


def _random_bot_username(owner_id: int, role: str = "inline") -> str:
    role_prefix = "h" if role == "helper" else ""
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"dtg{owner_id}_{role_prefix}{suffix}_bot"


def _botfather_retry_seconds(text: str) -> int:
    match = BOTFATHER_RETRY_RE.search(text or "")
    if not match:
        return 0
    try:
        return max(1, int(match.group(1)))
    except Exception:
        return 5


def _language() -> str:
    lang = str(profile_settings().get("language", "en")).strip().lower()
    return lang if lang in {"en", "ru"} else "en"


def _msg(en: str, ru: str) -> str:
    return ru if _language() == "ru" else en


def manual_bot_blueprints(owner_id: int) -> list[dict[str, str]]:
    return [
        {
            "slot": "1",
            "role": "inline",
            "label": "Inline bot",
            "env_key": "BOT_TOKEN",
            "display_name": f"DeathTG Inline {owner_id}",
            "username": f"dtg{owner_id}_inline_bot",
            "purpose_en": "Main owner bot, startup actions, private panel link, inline bridge.",
            "purpose_ru": "Главный бот владельца, стартовые действия, приватная ссылка на панель, inline-мост.",
        },
        {
            "slot": "2",
            "role": "helper",
            "label": "Helper bot",
            "env_key": "BOT_TOKEN_HELPER",
            "display_name": f"DeathTG Helper {owner_id}",
            "username": f"dtg{owner_id}_helper_bot",
            "purpose_en": "Fallback delivery channel, helper notifications, extra Telegram bridge.",
            "purpose_ru": "Резервный канал доставки, helper-уведомления, дополнительный Telegram-мост.",
        },
        {
            "slot": "3",
            "role": "community",
            "label": "Community bot",
            "env_key": "BOT_TOKEN_COMMUNITY",
            "display_name": community_bot_display_name(),
            "username": preferred_community_bot_username(),
            "purpose_en": "Owner-only role verification for admin/developer approvals.",
            "purpose_ru": "Owner-only проверка ролей для подтверждения admin/developer.",
        },
    ]


def manual_bot_blueprint(owner_id: int, slot: int | str) -> dict[str, str] | None:
    slot_text = str(slot).strip()
    for item in manual_bot_blueprints(owner_id):
        if item["slot"] == slot_text:
            return item
    return None


def _slot_command_name(slot: str) -> str:
    return f".crebot{slot}"


def render_manual_bot_guide(owner_id: int, slot: int | str | None = None) -> str:
    blueprints = manual_bot_blueprints(owner_id)
    if slot is not None:
        selected = manual_bot_blueprint(owner_id, slot)
        blueprints = [selected] if selected else []
    if not blueprints:
        return _msg("Unknown bot slot.", "Неизвестный слот бота.")
    lines = [
        _msg("DeathTG bot recovery", "Восстановление ботов DeathTG"),
        "",
    ]
    for item in blueprints:
        lines.extend(
            [
                f"{item['slot']}. {item['label']}",
                f"Name: {item['display_name']}",
                f"Username: @{item['username']}",
                _msg(f"Purpose: {item['purpose_en']}", f"Назначение: {item['purpose_ru']}"),
                _msg(
                    f"After BotFather sends the token, save it with: {_slot_command_name(item['slot'])} <token>",
                    f"Когда BotFather пришлёт токен, сохрани его так: {_slot_command_name(item['slot'])} <token>",
                ),
                "",
            ]
        )
    return "\n".join(lines).strip()


def render_integrity_report(status: dict) -> str:
    bots = [item for item in list(status.get("bots") or []) if isinstance(item, dict)]
    lines = [
        _msg("DeathTG integrity report", "Отчёт целостности DeathTG"),
        "",
    ]
    if not bots:
        lines.append(_msg("No bot data collected yet.", "Данные по ботам ещё не собраны."))
        return "\n".join(lines)
    for item in bots:
        role = str(item.get("role") or "bot")
        label = {
            "inline": "Inline",
            "helper": "Helper",
            "community": "Community",
        }.get(role, role.title())
        ok = bool(item.get("configured")) and bool(item.get("valid_username")) and bool(item.get("start_ping")) and not item.get("error")
        lines.append(f"{'OK' if ok else 'FAIL'} {label}: @{item.get('username') or 'missing'}")
        if item.get("error"):
            lines.append(f"  {_msg('Reason', 'Причина')}: {item.get('error')}")
        if role in {"inline", "helper", "community"}:
            slot = {"inline": "1", "helper": "2", "community": "3"}[role]
            lines.append(f"  {_msg('Recovery', 'Восстановление')}: {_slot_command_name(slot)}")
    folder = status.get("folder") if isinstance(status.get("folder"), dict) else {}
    if folder:
        lines.extend(
            [
                "",
                f"{_msg('Folder', 'Папка')}: {'OK' if folder.get('ok') else 'FAIL'}",
            ]
        )
        if folder.get("error"):
            lines.append(f"  {_msg('Reason', 'Причина')}: {folder.get('error')}")
    shortcuts = status.get("shortcuts") if isinstance(status.get("shortcuts"), dict) else {}
    panel_url = str(shortcuts.get("panel_url") or "")
    if panel_url:
        lines.extend(["", f"Panel: {panel_url}"])
    return "\n".join(lines)


def _integrity_signature(status: dict) -> str:
    bots = [item for item in list(status.get("bots") or []) if isinstance(item, dict)]
    chunks: list[str] = []
    for item in bots:
        chunks.append(
            "|".join(
                [
                    str(item.get("role") or ""),
                    str(item.get("username") or ""),
                    str(item.get("configured") or ""),
                    str(item.get("valid_username") or ""),
                    str(item.get("start_ping") or ""),
                    str(item.get("error") or ""),
                ]
            )
        )
    return "||".join(chunks)


def _integrity_failures(status: dict) -> list[dict]:
    failures: list[dict] = []
    for item in list(status.get("bots") or []):
        if not isinstance(item, dict):
            continue
        if not item.get("configured") or not item.get("valid_username") or not item.get("start_ping") or item.get("error"):
            failures.append(item)
    return failures


async def _send_saved_message(client, text: str) -> tuple[bool, str | None]:
    try:
        await client.send_message("me", text)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _integrity_alert_text(owner_id: int, status: dict) -> str:
    failures = _integrity_failures(status)
    lines = [
        _msg("DeathTG found problems in its Telegram bot system.", "DeathTG нашёл проблемы в своей Telegram-системе."),
        "",
        render_integrity_report(status),
    ]
    if failures:
        lines.extend(
            [
                "",
                _msg("Manual recovery shortcuts:", "Быстрые команды для восстановления:"),
            ]
        )
        for item in failures:
            slot = {"inline": "1", "helper": "2", "community": "3"}.get(str(item.get("role") or ""))
            if not slot:
                continue
            guide = manual_bot_blueprint(owner_id, slot)
            if not guide:
                continue
            lines.append(f"{_slot_command_name(slot)} -> @{guide['username']}")
        lines.extend(
            [
                "",
                _msg(
                    "Create the missing bot in BotFather, copy the token, then paste it into one of the commands above.",
                    "Создай отсутствующего бота в BotFather, скопируй токен и вставь его в одну из команд выше.",
                )
            ]
        )
    return "\n".join(lines)


async def _notify_integrity_if_needed(client, owner_id: int, status: dict, previous_status: dict | None = None) -> None:
    previous_status = previous_status or {}
    integrity = previous_status.get("integrity", {}) if isinstance(previous_status.get("integrity"), dict) else {}
    previous_signature = str(integrity.get("last_alert_signature") or "")
    current_signature = _integrity_signature(status)
    failures = _integrity_failures(status)
    if failures:
        if current_signature != previous_signature:
            _, error = await _send_saved_message(client, _integrity_alert_text(owner_id, status))
            status["integrity"] = {
                "last_alert_signature": current_signature,
                "last_alert_error": error,
                "healthy": False,
            }
            return
        status["integrity"] = {
            "last_alert_signature": previous_signature,
            "last_alert_error": integrity.get("last_alert_error"),
            "healthy": False,
        }
        return
    if previous_signature:
        text = _msg(
            "DeathTG integrity recovered. All configured Telegram bots respond again.",
            "Целостность DeathTG восстановлена. Все настроенные Telegram-боты снова отвечают.",
        )
        _, error = await _send_saved_message(client, text)
        status["integrity"] = {
            "last_alert_signature": "",
            "last_alert_error": error,
            "healthy": True,
        }
        return
    status["integrity"] = {"last_alert_signature": "", "last_alert_error": None, "healthy": True}


async def _botfather_step(conv, message: str, *, retries: int = 4):
    last_response = None
    for _ in range(max(1, retries)):
        await conv.send_message(message)
        response = await conv.get_response()
        last_response = response
        raw_text = getattr(response, "raw_text", "") or ""
        wait_seconds = _botfather_retry_seconds(raw_text)
        if wait_seconds:
            await asyncio.sleep(wait_seconds + 1)
            continue
        return response
    return last_response


def _peer_key(peer) -> tuple:
    if hasattr(peer, "channel_id"):
        return ("channel", getattr(peer, "channel_id"))
    if hasattr(peer, "chat_id"):
        return ("chat", getattr(peer, "chat_id"))
    if hasattr(peer, "user_id"):
        return ("user", getattr(peer, "user_id"))
    return (peer.__class__.__name__, repr(peer))


def _title_text(value) -> str:
    if isinstance(value, str):
        return value
    return getattr(value, "text", "") or ""


async def _fetch_bot_username(bot_token: str) -> tuple[str, str | None]:
    if not bot_token:
        return "", "BOT_TOKEN is missing"
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=12) as response:
                if response.status != 200:
                    return "", f"getMe HTTP {response.status}"
                data = await response.json()
    except Exception as exc:
        return "", str(exc)
    if not isinstance(data, dict):
        return "", "getMe returned invalid JSON"
    if not data.get("ok"):
        return "", str(data.get("description") or "getMe returned ok=false")
    result = data.get("result")
    if not isinstance(result, dict):
        return "", "getMe returned no result"
    return str(result.get("username") or "").strip(), None


async def _create_bot_with_botfather(client, owner_id: int, role: str = "inline") -> tuple[str, str | None]:
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass
    try:
        async with client.conversation("BotFather", timeout=120, exclusive=False) as conv:
            with contextlib.suppress(Exception):
                await _botfather_step(conv, "/cancel", retries=1)
            first = await _botfather_step(conv, "/newbot")
            first_text = getattr(first, "raw_text", "") or ""
            if "create and manage telegram bots" in first_text.lower():
                first = await _botfather_step(conv, "/newbot")
            with contextlib.suppress(Exception):
                getattr(first, "raw_text", "")
            display_role = "Helper" if role == "helper" else "Inline"
            await _botfather_step(conv, f"DeathTG {display_role} {owner_id}")
            for _ in range(20):
                response = await _botfather_step(conv, _random_bot_username(owner_id, role))
                text = getattr(response, "raw_text", "") or ""
                match = BOT_TOKEN_RE.search(text)
                if match:
                    return match.group(0), None
                lower = text.lower()
                if all(word not in lower for word in ("taken", "sorry", "username", "invalid")):
                    return "", text[:240]
    except Exception as exc:
        return "", str(exc)
    return "", "BotFather did not return a token"


async def _create_named_bot_with_botfather(client, display_name: str, username: str) -> tuple[str, str | None]:
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass
    try:
        async with client.conversation("BotFather", timeout=120, exclusive=False) as conv:
            with contextlib.suppress(Exception):
                await _botfather_step(conv, "/cancel", retries=1)
            first = await _botfather_step(conv, "/newbot")
            first_text = getattr(first, "raw_text", "") or ""
            if "create and manage telegram bots" in first_text.lower():
                first = await _botfather_step(conv, "/newbot")
            await _botfather_step(conv, display_name)
            response = await _botfather_step(conv, username)
            text = getattr(response, "raw_text", "") or ""
            match = BOT_TOKEN_RE.search(text)
            if match:
                return match.group(0), None
            return "", text[:240] or "BotFather did not return a token"
    except Exception as exc:
        return "", str(exc)


async def _set_bot_profile(bot_token: str, owner_id: int, role: str = "inline") -> tuple[bool, str | None]:
    if not bot_token:
        return False, "missing bot token"
    base = f"https://api.telegram.org/bot{bot_token}"
    role_title = "helper" if role == "helper" else "inline"
    commands = {
        "commands": [
            {"command": "start", "description": f"DeathTG {role_title} bot"},
            {"command": "status", "description": "Runtime status"},
        ]
    }
    description = {"description": f"DeathTG {role_title} bot for owner {owner_id}"}
    short_description = {"short_description": f"DeathTG {role_title} runtime"}
    try:
        async with aiohttp.ClientSession() as session:
            for method, payload in (
                ("setMyCommands", commands),
                ("setMyDescription", description),
                ("setMyShortDescription", short_description),
            ):
                async with session.post(f"{base}/{method}", json=payload, timeout=12) as response:
                    if response.status != 200:
                        return False, f"{method} HTTP {response.status}"
                    data = await response.json()
                    if not data.get("ok"):
                        return False, str(data.get("description") or f"{method} failed")
    except Exception as exc:
        return False, str(exc)
    return True, None


async def _sync_bot_avatar(client, bot_username: str) -> tuple[bool, str | None]:
    if not bot_username:
        return False, "missing bot username"
    if not BOT_AVATAR.exists():
        return True, None
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass
    try:
        async with client.conversation("BotFather", timeout=120, exclusive=False) as conv:
            with contextlib.suppress(Exception):
                await _botfather_step(conv, "/cancel", retries=1)
            await _botfather_step(conv, "/setuserpic")
            await _botfather_step(conv, f"@{bot_username}")
            await conv.send_file(str(BOT_AVATAR))
            with contextlib.suppress(Exception):
                await conv.get_response()
    except Exception as exc:
        return False, str(exc)
    return True, None


async def _ensure_bot_inline(client, bot_username: str) -> tuple[bool, str | None]:
    if not bot_username:
        return False, "missing bot username"
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass

    try:
        async with client.conversation("BotFather", timeout=120, exclusive=False) as conv:
            with contextlib.suppress(Exception):
                await _botfather_step(conv, "/cancel", retries=1)

            first = await _botfather_step(conv, "/setinline")
            first_text = (getattr(first, "raw_text", "") or "").lower()
            if "choose a bot" not in first_text and "select a bot" not in first_text and "@" not in first_text:
                return False, (getattr(first, "raw_text", "") or "BotFather did not ask for a bot")[:240]

            second = await _botfather_step(conv, f"@{bot_username}")
            second_text = (getattr(second, "raw_text", "") or "").lower()
            if any(word in second_text for word in ("placeholder", "input field", "inline")):
                final = await _botfather_step(conv, "DeathTG")
                final_text = getattr(final, "raw_text", "") or ""
            else:
                final_text = getattr(second, "raw_text", "") or ""

            lower = final_text.lower()
            if any(word in lower for word in ("success", "enabled", "updated", "changed")):
                return True, None
            if "already" in lower and "inline" in lower:
                return True, None
            return False, final_text[:240] or "BotFather did not confirm inline mode"
    except Exception as exc:
        return False, str(exc)


async def _archive_bot_dialog(client, bot_username: str) -> tuple[bool, str | None]:
    if not bot_username:
        return False, "missing bot username"
    try:
        bot_peer = await client.get_input_entity(bot_username)
        await client(EditPeerFoldersRequest(folder_peers=[InputFolderPeer(bot_peer, 1)]))
        return True, None
    except Exception as exc:
        return False, str(exc)


async def _ping_bot_runtime(client, username: str) -> tuple[bool, str | None]:
    if not username:
        return False, "missing bot username"
    try:
        await client.send_message(username, "/start")
        return True, None
    except Exception as exc:
        return False, str(exc)


async def check_runtime_integrity(client, *, notify: bool = True) -> dict:
    me = await client.get_me()
    owner_id = int(getattr(me, "id", 0) or 0)
    previous_status = _load_status()
    bots: list[dict] = []
    for blueprint in manual_bot_blueprints(owner_id):
        if blueprint["role"] == "community" and not community_enabled_for_owner(owner_id):
            continue
        token = _env(blueprint["env_key"])
        username, token_error = await _fetch_bot_username(token)
        if blueprint["role"] == "community":
            valid_username = bool(username and username.lower() == blueprint["username"].lower())
        else:
            valid_username = _is_valid_bot_username(username, owner_id)
        start_ping = False
        start_ping_error = None
        if valid_username:
            start_ping, start_ping_error = await _ping_bot_runtime(client, username)
        bots.append(
            {
                "configured": bool(token),
                "role": blueprint["role"],
                "env_key": blueprint["env_key"],
                "username": username,
                "created": False,
                "valid_username": valid_username,
                "expected_prefix": blueprint["username"],
                "owner_id": owner_id,
                "commands_synced": None,
                "inline_synced": None,
                "avatar_synced": None,
                "start_ping": start_ping,
                "archived": None,
                "error": token_error or start_ping_error,
            }
        )
    runtime_status = dict(previous_status) if isinstance(previous_status, dict) else {}
    runtime_status["bots"] = bots
    runtime_status["bot"] = next((item for item in bots if item.get("role") == "inline"), {})
    runtime_status["helper_bot"] = next((item for item in bots if item.get("role") == "helper"), {})
    runtime_status["community_bot"] = next((item for item in bots if item.get("role") == "community"), {})
    runtime_status["last_runtime_check_at"] = int(time.time())
    if notify:
        await _notify_integrity_if_needed(client, owner_id, runtime_status, previous_status)
    _write_status(runtime_status)
    return runtime_status


async def _ensure_folder(client, peers: list) -> tuple[bool, str | None]:
    if not peers:
        return False, "no peers for folder"
    try:
        filters = await client(GetDialogFiltersRequest())
    except Exception as exc:
        return False, str(exc)

    existing = None
    used_ids: set[int] = set()
    filter_items = list(getattr(filters, "filters", filters) or [])
    for item in filter_items:
        if not item:
            continue
        item_id = int(getattr(item, "id", 0) or 0)
        if item_id:
            used_ids.add(item_id)
        if _title_text(getattr(item, "title", "")) == FOLDER_NAME:
            existing = item

    include_peers: list = []
    seen: set[tuple] = set()
    for peer in peers:
        key = _peer_key(peer)
        if key in seen:
            continue
        seen.add(key)
        include_peers.append(peer)

    filter_id = int(getattr(existing, "id", 0) or 0)
    if not filter_id:
        filter_id = next((idx for idx in range(2, 255) if idx not in used_ids), 2)

    dialog_filter = DialogFilter(
        id=filter_id,
        title=TextWithEntities(FOLDER_NAME, []),
        pinned_peers=[],
        include_peers=include_peers,
        exclude_peers=[],
        contacts=False,
        non_contacts=False,
        groups=False,
        broadcasts=False,
        bots=False,
        exclude_muted=False,
        exclude_read=False,
        exclude_archived=False,
        title_noanimate=getattr(existing, "title_noanimate", False) if existing else False,
        emoticon=getattr(existing, "emoticon", None) if existing else None,
        color=getattr(existing, "color", None) if existing else None,
    )
    try:
        await client(UpdateDialogFilterRequest(id=filter_id, filter=dialog_filter))
    except Exception as exc:
        return False, str(exc)
    return True, None


async def _ensure_bot(
    client,
    bot_token: str,
    owner_id: int,
    *,
    env_key: str = "BOT_TOKEN",
    role: str = "inline",
) -> tuple[str, dict]:
    username, token_error = await _fetch_bot_username(bot_token)
    status = {
        "configured": bool(bot_token),
        "role": role,
        "env_key": env_key,
        "username": username,
        "created": False,
        "valid_username": _is_valid_bot_username(username, owner_id),
        "expected_prefix": _expected_prefix(owner_id),
        "owner_id": owner_id,
        "error": token_error,
    }
    if status["valid_username"]:
        status["error"] = None
        return bot_token, status

    token, error = await _create_bot_with_botfather(client, owner_id, role)
    if not token:
        status["error"] = error or token_error or "unable to create owner-bound bot"
        return bot_token, status

    update_env_value(env_key, token)
    username, token_error = await _fetch_bot_username(token)
    status.update(
        {
            "configured": True,
            "username": username,
            "created": True,
            "valid_username": _is_valid_bot_username(username, owner_id),
            "error": token_error,
        }
    )
    if not status["valid_username"] and not status["error"]:
        status["error"] = "new bot username does not match expected owner prefix"
    return token, status


async def _ensure_community_bot(client, owner_id: int) -> tuple[str, dict]:
    username_target = preferred_community_bot_username()
    bot_token = _env("BOT_TOKEN_COMMUNITY")
    username, token_error = await _fetch_bot_username(bot_token)
    status = {
        "configured": bool(bot_token),
        "role": "community",
        "env_key": "BOT_TOKEN_COMMUNITY",
        "username": username,
        "created": False,
        "valid_username": bool(username),
        "expected_prefix": username_target,
        "owner_id": owner_id,
        "error": token_error,
    }
    if status["valid_username"]:
        update_env_value("COMMUNITY_BOT_USERNAME", username)
        status["error"] = None
        return bot_token, status
    token, error = await _create_named_bot_with_botfather(
        client,
        community_bot_display_name(),
        username_target,
    )
    if not token:
        status["error"] = error or token_error or "unable to create community bot"
        return bot_token, status
    update_env_value("BOT_TOKEN_COMMUNITY", token)
    update_env_value("COMMUNITY_BOT_USERNAME", username_target)
    username, token_error = await _fetch_bot_username(token)
    status.update(
        {
            "configured": True,
            "username": username,
            "created": True,
            "valid_username": bool(username and username.lower() == username_target.lower()),
            "error": token_error,
        }
    )
    if not status["valid_username"] and not status["error"]:
        status["error"] = f"community bot username must be @{username_target}"
    return token, status


async def run_startup_sync(client) -> dict:
    me = await client.get_me()
    owner_id = int(getattr(me, "id", 0) or 0)
    update_env_value("OWNER_ID", str(owner_id))
    bot_token = _env("BOT_TOKEN")
    helper_token = _env("BOT_TOKEN_HELPER")
    community_token = _env("BOT_TOKEN_COMMUNITY")

    bot_token, bot_status = await _ensure_bot(
        client,
        bot_token,
        owner_id,
        env_key="BOT_TOKEN",
        role="inline",
    )
    helper_token, helper_status = await _ensure_bot(
        client,
        helper_token,
        owner_id,
        env_key="BOT_TOKEN_HELPER",
        role="helper",
    )
    community_status = {
        "configured": False,
        "role": "community",
        "env_key": "BOT_TOKEN_COMMUNITY",
        "username": _env("COMMUNITY_BOT_USERNAME"),
        "created": False,
        "valid_username": False,
        "expected_prefix": preferred_community_bot_username(),
        "owner_id": owner_id,
        "error": "Community bot is owner-only",
        "commands_synced": False,
        "inline_synced": False,
        "avatar_synced": False,
        "start_ping": False,
        "archived": False,
    }
    if community_enabled_for_owner(owner_id):
        community_token, community_status = await _ensure_community_bot(client, owner_id)
    bot_username = str(bot_status.get("username") or "")
    helper_username = str(helper_status.get("username") or "")
    community_username = str(community_status.get("username") or "")

    commands_synced, commands_error = await _set_bot_profile(bot_token, owner_id, "inline")
    inline_synced, inline_error = await _ensure_bot_inline(client, bot_username)
    avatar_synced, avatar_error = await _sync_bot_avatar(client, bot_username)
    bot_status["commands_synced"] = commands_synced
    bot_status["inline_synced"] = inline_synced
    bot_status["avatar_synced"] = avatar_synced

    helper_commands_synced, helper_commands_error = await _set_bot_profile(helper_token, owner_id, "helper")
    helper_avatar_synced, helper_avatar_error = await _sync_bot_avatar(client, helper_username)
    helper_status["commands_synced"] = helper_commands_synced
    helper_status["inline_synced"] = False
    helper_status["avatar_synced"] = helper_avatar_synced

    community_commands_synced = False
    community_commands_error = None
    community_avatar_synced = False
    community_avatar_error = None
    if community_enabled_for_owner(owner_id):
        community_commands_synced, community_commands_error = await _set_bot_profile(community_token, owner_id, "community")
        community_avatar_synced, community_avatar_error = await _sync_bot_avatar(client, community_username)
    community_status["commands_synced"] = community_commands_synced
    community_status["inline_synced"] = False
    community_status["avatar_synced"] = community_avatar_synced

    def _collect_error(*items: str | None) -> str | None:
        return next((item for item in items if item), None)

    start_ping, start_ping_error = await _ping_bot_runtime(client, bot_username)
    helper_start_ping, helper_start_ping_error = await _ping_bot_runtime(client, helper_username)
    community_start_ping = False
    community_start_ping_error = None
    if community_enabled_for_owner(owner_id):
        community_start_ping, community_start_ping_error = await _ping_bot_runtime(client, community_username)
    bot_status["start_ping"] = start_ping
    helper_status["start_ping"] = helper_start_ping
    community_status["start_ping"] = community_start_ping

    archived, archive_error = await _archive_bot_dialog(client, bot_username)
    helper_archived, helper_archive_error = await _archive_bot_dialog(client, helper_username)
    community_archived = False
    community_archive_error = None
    if community_enabled_for_owner(owner_id):
        community_archived, community_archive_error = await _archive_bot_dialog(client, community_username)
    bot_status["archived"] = archived
    helper_status["archived"] = helper_archived
    community_status["archived"] = community_archived

    if not bot_status.get("error"):
        bot_status["error"] = _collect_error(commands_error, inline_error, avatar_error, archive_error, start_ping_error)
    if not helper_status.get("error"):
        helper_status["error"] = _collect_error(
            helper_commands_error,
            helper_avatar_error,
            helper_archive_error,
            helper_start_ping_error,
        )
    if community_enabled_for_owner(owner_id) and not community_status.get("error"):
        community_status["error"] = _collect_error(
            community_commands_error,
            community_avatar_error,
            community_archive_error,
            community_start_ping_error,
        )

    status = {
        "bot": bot_status,
        "helper_bot": helper_status,
        "community_bot": community_status,
        "bots": [bot_status, helper_status] + ([community_status] if community_enabled_for_owner(owner_id) else []),
        "channels": [],
        "folder": {
            "name": FOLDER_NAME,
            "ok": False,
            "error": None,
            "include_count": 0,
            "include_usernames": [],
        },
        "last_sync_at": int(time.time()),
        "last_sync_error": None,
        "shortcuts": {"sent": False, "error": None, "panel_url": "", "sent_at": 0, "interval_sec": 0},
    }
    previous_status = _load_status()
    previous_shortcuts = previous_status.get("shortcuts", {}) if isinstance(previous_status, dict) else {}
    previous_sent_at = int(previous_shortcuts.get("sent_at", 0) or 0) if isinstance(previous_shortcuts, dict) else 0

    folder_peers: list = []
    include_usernames: list[str] = []
    for username in (bot_username, helper_username, community_username if community_enabled_for_owner(owner_id) else ""):
        if not username:
            continue
        try:
            bot_entity = await client.get_entity(username)
            bot_peer = await client.get_input_entity(bot_entity)
            with contextlib.suppress(Exception):
                await client(UnblockRequest(bot_peer))
            folder_peers.append(bot_peer)
            include_usernames.append(f"@{username}")
        except Exception as exc:
            if not status["last_sync_error"]:
                status["last_sync_error"] = str(exc)

    for channel_name in TARGET_CHANNELS:
        row = {"username": channel_name, "joined": False, "title": "", "error": None}
        try:
            entity = await client.get_entity(channel_name)
            row["title"] = getattr(entity, "title", "") or f"@{channel_name}"
            try:
                await client(JoinChannelRequest(entity))
            except Exception:
                pass
            channel_peer = await client.get_input_entity(entity)
            folder_peers.append(channel_peer)
            include_usernames.append(f"@{channel_name}")
            row["joined"] = True
        except Exception as exc:
            row["error"] = str(exc)
        status["channels"].append(row)

    folder_ok, folder_error = await _ensure_folder(client, folder_peers)
    status["folder"]["ok"] = folder_ok
    status["folder"]["error"] = folder_error
    status["folder"]["include_count"] = len(folder_peers)
    status["folder"]["include_usernames"] = include_usernames

    channel_error = next((item.get("error") for item in status["channels"] if item.get("error")), None)
    status["last_sync_error"] = (
        status["last_sync_error"]
        or folder_error
        or bot_status.get("error")
        or helper_status.get("error")
        or channel_error
    )

    async def _send_owner_shortcuts() -> tuple[bool, str | None, str]:
        if _env("PANEL_SHORTCUTS_ON_STARTUP") == "0":
            return False, "disabled by PANEL_SHORTCUTS_ON_STARTUP=0", ""
        allowed, cooldown_reason, _ = _shortcuts_allowed_now()
        if not allowed:
            return False, cooldown_reason, ""
        panel_url = _build_panel_grant_url(owner_id)
        remote_ready = panel_remote_access_ready()
        news_url = _env("PANEL_NEWS_URL")
        support_url = _env("PANEL_SUPPORT_URL")
        personal_url = _env("PANEL_PERSONAL_URL")
        buttons: list[list[dict]] = [[{"text": "Open DeathTG", "url": panel_url}]]
        second_row: list[dict] = []
        if news_url:
            second_row.append({"text": "News", "url": news_url})
        if support_url:
            second_row.append({"text": "Support", "url": support_url})
        if second_row:
            buttons.append(second_row)
        if personal_url:
            buttons.append([{"text": "Personal Site", "url": personal_url}])
        remote_note = ""
        if not remote_ready:
            remote_note = (
                "\n\nRemote phone access is not enabled yet because the panel is still local-only. "
                "Restart DeathTG after this update so it can rebind the panel and refresh your secure links."
            )
        payload = {
            "chat_id": owner_id,
            "caption": "".join(
                [
                    "Welcome to DeathTG.\n\n",
                    "Your personal private panel link is ready.\n",
                    "Do not share this link with anyone.\n",
                    "Open this link on your phone or on another browser if you want to trust one more device.\n",
                    "If you need another device later, create a new secure link from inside the panel.",
                    remote_note,
                ]
            ),
            "reply_markup": {"inline_keyboard": buttons},
            "disable_web_page_preview": True,
        }
        welcome_image = system_image("welcome")
        if welcome_image and welcome_image.exists():
            payload["photo"] = str(welcome_image)
        else:
            payload["text"] = payload.pop("caption")
        token_candidates: list[tuple[str, str]] = []
        if helper_token:
            token_candidates.append(("helper", helper_token))
        if bot_token:
            token_candidates.append(("inline", bot_token))
        if community_token and community_enabled_for_owner(owner_id):
            token_candidates.append(("community", community_token))
        errors: list[str] = []

        async def _try_send_via_bot(label: str, token: str) -> tuple[bool, str | None]:
            if not token:
                return False, "missing token"
            if payload.get("photo"):
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
            else:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
            for attempt in range(2):
                try:
                    async with aiohttp.ClientSession() as session:
                        if payload.get("photo"):
                            form_data = aiohttp.FormData()
                            form_data.add_field("chat_id", str(owner_id))
                            form_data.add_field("caption", str(payload.get("caption") or ""))
                            form_data.add_field("reply_markup", json.dumps(payload["reply_markup"], ensure_ascii=False))
                            form_data.add_field("disable_web_page_preview", "true")
                            form_data.add_field("photo", welcome_image.read_bytes(), filename=welcome_image.name, content_type="image/png")
                            async with session.post(url, data=form_data, timeout=20) as response:
                                if response.status != 200:
                                    return False, f"{label}: sendPhoto HTTP {response.status}"
                                data = await response.json()
                        else:
                            async with session.post(url, json=payload, timeout=12) as response:
                                if response.status != 200:
                                    return False, f"{label}: sendMessage HTTP {response.status}"
                                data = await response.json()
                    if data.get("ok"):
                        return True, None
                    description = str(data.get("description") or "bot send failed")
                    if "initiate conversation" in description.lower() and attempt == 0:
                        await asyncio.sleep(2)
                        continue
                    return False, f"{label}: {description}"
                except Exception as exc:
                    if attempt == 0:
                        await asyncio.sleep(1)
                        continue
                    return False, f"{label}: {exc}"
            return False, f"{label}: bot send failed"

        for label, token in token_candidates:
            ok, error = await _try_send_via_bot(label, token)
            if ok:
                return True, None, panel_url
            if error:
                errors.append(error)

        try:
            direct_lines = [
                "Welcome to DeathTG.",
                "",
                "Your personal private panel link is ready.",
                "Do not share this link with anyone.",
                panel_url,
            ]
            if remote_note:
                direct_lines.extend(["", remote_note.strip()])
            await client.send_message("me", "\n".join(direct_lines))
            return True, "Bot delivery failed, shortcut was sent to Saved Messages", panel_url
        except Exception as exc:
            errors.append(f"userbot-direct: {exc}")
        return False, " | ".join(errors) if errors else "missing bot token", panel_url

    shortcuts_sent, shortcuts_error, panel_url = await _send_owner_shortcuts()
    status["shortcuts"]["sent"] = shortcuts_sent
    status["shortcuts"]["error"] = shortcuts_error
    status["shortcuts"]["panel_url"] = panel_url
    status["shortcuts"]["interval_sec"] = _shortcuts_interval_seconds()
    status["shortcuts"]["sent_at"] = int(time.time()) if shortcuts_sent else previous_sent_at
    await _notify_integrity_if_needed(client, owner_id, status, previous_status)
    _write_status(status)
    return status
