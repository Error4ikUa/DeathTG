from __future__ import annotations

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
from deathtg.profile_store import update_env_value


TARGET_CHANNELS = ("Death_Telega", "Death_TgOfftop")
FOLDER_NAME = "DeathTG"
STATUS_PATH = RUNTIME_DIR / "startup_status.json"
PANEL_GRANTS_PATH = RUNTIME_DIR / "panel_grants.json"
BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
BOT_AVATAR = ROOT_DIR / "deathtg" / "panel" / "static" / "user" / "avatar.png"


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


def _load_panel_grants() -> dict:
    if not PANEL_GRANTS_PATH.exists():
        return {}
    try:
        data = json.loads(PANEL_GRANTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_panel_grants(data: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PANEL_GRANTS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_panel_grants(data: dict) -> dict:
    now = int(time.time())
    cleaned = {}
    for token, item in data.items():
        if not isinstance(item, dict):
            continue
        expires_at = int(item.get("expires_at", 0) or 0)
        used = bool(item.get("used"))
        if used:
            continue
        if expires_at and expires_at < now:
            continue
        cleaned[str(token)] = item
    # Keep file bounded even on long-running hosts.
    if len(cleaned) > 256:
        items = sorted(
            cleaned.items(),
            key=lambda item: int((item[1] or {}).get("created_at", 0) or 0),
            reverse=True,
        )
        return dict(items[:256])
    return cleaned


def _issue_panel_grant(owner_id: int, ttl_seconds: int = 60 * 60 * 24 * 7) -> str:
    token = secrets.token_urlsafe(24)
    now = int(time.time())
    data = _cleanup_panel_grants(_load_panel_grants())
    data[token] = {
        "owner_id": int(owner_id),
        "created_at": now,
        "expires_at": now + int(ttl_seconds),
        "used": False,
    }
    _save_panel_grants(data)
    return token


def _panel_base_url() -> str:
    full = _env("PANEL_PUBLIC_URL")
    if full:
        return full.rstrip("/")
    scheme = _env("PANEL_SCHEME") or "http"
    host = _env("PANEL_PUBLIC_HOST") or _env("PANEL_HOST") or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = _env("PANEL_PORT") or "8080"
    if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _build_panel_grant_url(owner_id: int) -> str:
    token = _issue_panel_grant(owner_id)
    return f"{_panel_base_url()}/grant/{token}"


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
                await conv.send_message("/cancel")
                await conv.get_response()
            await conv.send_message("/newbot")
            with contextlib.suppress(Exception):
                await conv.get_response()
            display_role = "Helper" if role == "helper" else "Inline"
            await conv.send_message(f"DeathTG {display_role} {owner_id}")
            with contextlib.suppress(Exception):
                await conv.get_response()
            for _ in range(20):
                await conv.send_message(_random_bot_username(owner_id, role))
                response = await conv.get_response()
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
                await conv.send_message("/cancel")
                await conv.get_response()
            await conv.send_message("/setuserpic")
            with contextlib.suppress(Exception):
                await conv.get_response()
            await conv.send_message(f"@{bot_username}")
            with contextlib.suppress(Exception):
                await conv.get_response()
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
                await conv.send_message("/cancel")
                await conv.get_response()

            await conv.send_message("/setinline")
            first = await conv.get_response()
            first_text = (getattr(first, "raw_text", "") or "").lower()
            if "choose a bot" not in first_text and "select a bot" not in first_text and "@" not in first_text:
                return False, (getattr(first, "raw_text", "") or "BotFather did not ask for a bot")[:240]

            await conv.send_message(f"@{bot_username}")
            second = await conv.get_response()
            second_text = (getattr(second, "raw_text", "") or "").lower()
            if any(word in second_text for word in ("placeholder", "input field", "inline")):
                await conv.send_message("DeathTG")
                final = await conv.get_response()
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


async def run_startup_sync(client) -> dict:
    me = await client.get_me()
    owner_id = int(getattr(me, "id", 0) or 0)
    bot_token = _env("BOT_TOKEN")
    helper_token = _env("BOT_TOKEN_HELPER")

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
    bot_username = str(bot_status.get("username") or "")
    helper_username = str(helper_status.get("username") or "")

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

    def _collect_error(*items: str | None) -> str | None:
        return next((item for item in items if item), None)

    async def _ping_bot(username: str) -> tuple[bool, str | None]:
        if not username:
            return False, "missing bot username"
        try:
            await client.send_message(username, "/start")
            return True, None
        except Exception as exc:
            return False, str(exc)

    start_ping, start_ping_error = await _ping_bot(bot_username)
    helper_start_ping, helper_start_ping_error = await _ping_bot(helper_username)
    bot_status["start_ping"] = start_ping
    helper_status["start_ping"] = helper_start_ping

    archived, archive_error = await _archive_bot_dialog(client, bot_username)
    helper_archived, helper_archive_error = await _archive_bot_dialog(client, helper_username)
    bot_status["archived"] = archived
    helper_status["archived"] = helper_archived

    if not bot_status.get("error"):
        bot_status["error"] = _collect_error(commands_error, inline_error, avatar_error, archive_error, start_ping_error)
    if not helper_status.get("error"):
        helper_status["error"] = _collect_error(
            helper_commands_error,
            helper_avatar_error,
            helper_archive_error,
            helper_start_ping_error,
        )

    status = {
        "bot": bot_status,
        "helper_bot": helper_status,
        "bots": [bot_status, helper_status],
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
    for username in (bot_username, helper_username):
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
        token = helper_token or bot_token
        if not token:
            return False, "missing bot token", ""
        panel_url = _build_panel_grant_url(owner_id)
        news_url = _env("PANEL_NEWS_URL")
        support_url = _env("PANEL_SUPPORT_URL")
        personal_url = _env("PANEL_PERSONAL_URL")
        buttons: list[list[dict]] = [[{"text": "Open Panel", "url": panel_url}]]
        second_row: list[dict] = []
        if news_url:
            second_row.append({"text": "News", "url": news_url})
        if support_url:
            second_row.append({"text": "Support", "url": support_url})
        if second_row:
            buttons.append(second_row)
        if personal_url:
            buttons.append([{"text": "Personal Site", "url": personal_url}])
        payload = {
            "chat_id": owner_id,
            "text": "DeathTG is connected. Open your control panel:",
            "reply_markup": {"inline_keyboard": buttons},
            "disable_web_page_preview": True,
        }
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=12) as response:
                    if response.status != 200:
                        return False, f"sendMessage HTTP {response.status}", panel_url
                    data = await response.json()
            if not data.get("ok"):
                return False, str(data.get("description") or "sendMessage failed"), panel_url
            return True, None, panel_url
        except Exception as exc:
            return False, str(exc), panel_url

    shortcuts_sent, shortcuts_error, panel_url = await _send_owner_shortcuts()
    status["shortcuts"]["sent"] = shortcuts_sent
    status["shortcuts"]["error"] = shortcuts_error
    status["shortcuts"]["panel_url"] = panel_url
    status["shortcuts"]["interval_sec"] = _shortcuts_interval_seconds()
    status["shortcuts"]["sent_at"] = int(time.time()) if shortcuts_sent else previous_sent_at
    _write_status(status)
    return status
