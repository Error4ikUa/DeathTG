from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import string
import time

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
from deathtg.premium_emoji import emoji_line
from deathtg.profile_store import profile_settings, update_env_value


TARGET_CHANNELS = ("Death_Telega", "Death_TgOfftop")
FOLDER_NAME = "DeathTG"
STATUS_PATH = RUNTIME_DIR / "startup_status.json"
BOT_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
BOTFATHER_RETRY_RE = re.compile(r"too many attempts.*?try again in\s+(\d+)\s+seconds?", re.IGNORECASE)
BOTFATHER_NETWORK_RE = re.compile(
    r"(WinError\s+(?:121|10054|1231|1236)|timeout|timed out|network|connection|semaphore)",
    re.IGNORECASE,
)
BOT_AVATAR = default_avatar_path() or (ROOT_DIR / "deathtg" / "panel" / "static" / "default_avatar.png")
BOTFATHER_CREATE_TIMEOUT = 35
AUTO_BOT_REPAIR_INTERVAL = 60 * 15
BOTFATHER_PROCESS_LOCK = RUNTIME_DIR / "botfather.lock"
_STARTUP_SYNC_LOCK = asyncio.Lock()
_BOTFATHER_LOCK = asyncio.Lock()


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


@contextlib.asynccontextmanager
async def _botfather_process_guard(action: str, *, wait_seconds: int = 18, stale_seconds: int = 240):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    acquired = False
    fd: int | None = None
    deadline = time.time() + max(0, wait_seconds)
    detail = f"{os.getpid()} {int(time.time())} {action}\n"
    while True:
        try:
            fd = os.open(str(BOTFATHER_PROCESS_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, detail.encode("utf-8", errors="replace"))
            acquired = True
            break
        except FileExistsError:
            try:
                age = time.time() - BOTFATHER_PROCESS_LOCK.stat().st_mtime
            except OSError:
                age = stale_seconds + 1
            if age > stale_seconds:
                with contextlib.suppress(OSError):
                    BOTFATHER_PROCESS_LOCK.unlink()
                continue
            if time.time() >= deadline:
                yield False, "BotFather is busy in another DeathTG process; retry after the current sync finishes"
                return
            await asyncio.sleep(0.5)
        except OSError as exc:
            yield False, f"BotFather lock failed: {exc}"
            return
    try:
        yield True, None
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if acquired:
            with contextlib.suppress(OSError):
                BOTFATHER_PROCESS_LOCK.unlink()


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
    role_prefix = {"inline": "i", "helper": "h", "community": "c"}.get(role, "x")
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
    return f"dtg{owner_id}_{role_prefix}{suffix}_bot"


def _bot_username_env_key(role: str) -> str:
    return {
        "inline": "INLINE_BOT_USERNAME",
        "helper": "HELPER_BOT_USERNAME",
        "community": "COMMUNITY_BOT_USERNAME",
    }.get(role, "INLINE_BOT_USERNAME")


def _preferred_bot_username(owner_id: int, role: str) -> str:
    if role == "community" and not community_enabled_for_owner(owner_id):
        return preferred_community_bot_username(owner_id)
    env_key = _bot_username_env_key(role)
    raw = _env(env_key).lstrip("@")
    if _is_valid_bot_username(raw, owner_id):
        return raw
    generated = _random_bot_username(owner_id, role)
    update_env_value(env_key, generated)
    return generated


def _legacy_bot_usernames(owner_id: int) -> set[str]:
    names = {
        f"dtg{owner_id}_inline_bot",
        f"dtg{owner_id}_helper_bot",
        preferred_community_bot_username(owner_id).lstrip("@"),
    }
    for env_key in ("INLINE_BOT_USERNAME", "HELPER_BOT_USERNAME", "COMMUNITY_BOT_USERNAME"):
        raw = _env(env_key).lstrip("@").strip()
        if raw:
            names.add(raw)
    return {name for name in names if name}


def _role_title_prefixes(role: str) -> tuple[str, ...]:
    return {
        "inline": ("deathtg inline", "inline"),
        "helper": ("deathtg helper", "helper"),
        "community": ("deathtg community", "deathterror community", "community", "comunity"),
    }.get(role, ())


def _role_username_hint(role: str) -> str:
    return {"inline": "_i", "helper": "_h", "community": "_c"}.get(role, "_")


def _status_bot_username(role: str) -> str:
    status = _load_status()
    for key in ("bots",):
        for item in list(status.get(key) or []):
            if isinstance(item, dict) and item.get("role") == role:
                return str(item.get("username") or "").lstrip("@").strip()
    direct_key = {"inline": "bot", "helper": "helper_bot", "community": "community_bot"}.get(role, "")
    item = status.get(direct_key) if direct_key else None
    if isinstance(item, dict):
        return str(item.get("username") or "").lstrip("@").strip()
    return ""


async def _discover_service_bot_usernames(client, owner_id: int, role: str) -> list[str]:
    usernames: list[str] = []
    seen: set[str] = set()
    env_username = _env(_bot_username_env_key(role)).lstrip("@").strip()
    status_username = _status_bot_username(role)
    for username in (env_username, status_username):
        if _is_valid_bot_username(username, owner_id) and username.lower() not in seen:
            seen.add(username.lower())
            usernames.append(username)
    role_hint = _role_username_hint(role)
    title_prefixes = _role_title_prefixes(role)
    async for dialog in client.iter_dialogs(ignore_pinned=False, archived=None):
        entity = getattr(dialog, "entity", None)
        if not entity or not getattr(entity, "bot", False):
            continue
        username = str(getattr(entity, "username", "") or "").strip().lstrip("@")
        if not _is_valid_bot_username(username, owner_id):
            continue
        title = str(getattr(entity, "title", "") or getattr(dialog, "name", "") or "").strip().lower()
        username_l = username.lower()
        matches_role = role_hint in username_l or any(title.startswith(prefix) for prefix in title_prefixes)
        if not matches_role or username_l in seen:
            continue
        seen.add(username_l)
        usernames.append(username)
    return usernames


async def _discover_service_bot_peers(client, owner_id: int, current_usernames: list[str]) -> tuple[list, list[str]]:
    include_peers: list = []
    include_usernames: list[str] = []
    seen: set[tuple] = set()
    known_usernames = {str(item).lstrip("@").strip().lower() for item in current_usernames if str(item).strip()}
    known_usernames.update(name.lower() for name in _legacy_bot_usernames(owner_id))
    generated_prefix = f"dtg{int(owner_id)}_"
    title_prefixes = tuple(
        prefix
        for role in ("inline", "helper", "community")
        for prefix in _role_title_prefixes(role)
    )
    async for dialog in client.iter_dialogs(ignore_pinned=False, archived=None):
        entity = getattr(dialog, "entity", None)
        if not entity or not getattr(entity, "bot", False):
            continue
        username = str(getattr(entity, "username", "") or "").strip()
        username_l = username.lower()
        title = str(getattr(entity, "title", "") or getattr(dialog, "name", "") or "").strip().lower()
        matches_username = bool(username and username_l in known_usernames)
        matches_generated = bool(username_l.startswith(generated_prefix) and username_l.endswith("bot"))
        matches_title = bool(str(owner_id) in title and any(title.startswith(prefix) for prefix in title_prefixes))
        if not (matches_username or matches_generated or matches_title):
            continue
        try:
            peer = await client.get_input_entity(entity)
        except Exception:
            continue
        key = _peer_key(peer)
        if key in seen:
            continue
        seen.add(key)
        include_peers.append(peer)
        include_usernames.append(f"@{username}" if username else getattr(dialog, "name", "") or "DeathTG bot")
    return include_peers, include_usernames


def _botfather_retry_seconds(text: str) -> int:
    match = BOTFATHER_RETRY_RE.search(text or "")
    if not match:
        return 0
    try:
        return max(1, int(match.group(1)))
    except Exception:
        return 5


def _botfather_status() -> dict:
    raw = _load_status().get("botfather", {})
    return raw if isinstance(raw, dict) else {}


def _botfather_cooldown_left() -> int:
    until = int(_botfather_status().get("cooldown_until", 0) or 0)
    return max(0, until - int(time.time()))


def _set_botfather_cooldown(wait_seconds: int, reason: str = "") -> None:
    if wait_seconds <= 0:
        return
    payload = _load_status()
    payload["botfather"] = {
        "cooldown_until": int(time.time()) + int(wait_seconds),
        "last_result": str(reason or f"cooldown ({wait_seconds}s left)")[:240],
        "updated_at": int(time.time()),
    }
    _write_status(payload)


def _clear_botfather_cooldown() -> None:
    payload = _load_status()
    if "botfather" not in payload:
        return
    payload.pop("botfather", None)
    _write_status(payload)


def _botfather_cooldown_error() -> str | None:
    wait_left = _botfather_cooldown_left()
    if wait_left <= 0:
        return None
    return f"BotFather cooldown active ({wait_left}s left)"


def _botfather_network_error(exc: Exception) -> str | None:
    text = f"{type(exc).__name__}: {exc}"
    if not BOTFATHER_NETWORK_RE.search(text):
        return None
    message = "Telegram network is unstable; BotFather auto-create paused for 30 minutes"
    _set_botfather_cooldown(60 * 30, text[:240])
    return message


def _language() -> str:
    lang = str(profile_settings().get("language", "en")).strip().lower()
    return lang if lang in {"en", "ru"} else "en"


def _msg(en: str, ru: str) -> str:
    mojibake_markers = (
        "Рџ",
        "Р’",
        "Р“",
        "Р”",
        "РЎ",
        "СЃ",
        "С‚",
        "С‹",
        "СЊ",
        "СЋ",
        "СЏ",
        "С‡",
        "С€",
        "С‰",
        "С…",
        "вЂ",
        "РІР‚",
        "Ѓ",
        "Џ",
        "Њ",
        "Љ",
    )
    if any(marker in ru for marker in mojibake_markers):
        return en
    return ru if _language() == "ru" else en


def manual_bot_blueprints(owner_id: int) -> list[dict[str, str]]:
    return [
        {
            "slot": "1",
            "role": "inline",
            "label": "Inline bot",
            "env_key": "BOT_TOKEN",
            "display_name": "DeathTG Inline",
            "username": _preferred_bot_username(owner_id, "inline"),
            "purpose_en": "Main owner bot, startup actions, private panel link, inline bridge.",
            "purpose_ru": "Главный бот владельца, стартовые действия, приватная ссылка на панель, inline-мост.",
        },
        {
            "slot": "2",
            "role": "helper",
            "label": "Helper bot",
            "env_key": "BOT_TOKEN_HELPER",
            "display_name": "DeathTG Helper",
            "username": _preferred_bot_username(owner_id, "helper"),
            "purpose_en": "Fallback delivery channel, helper notifications, extra Telegram bridge.",
            "purpose_ru": "Резервный канал доставки, helper-уведомления, дополнительный Telegram-мост.",
        },
        {
            "slot": "3",
            "role": "community",
            "label": "Community bot",
            "env_key": "BOT_TOKEN_COMMUNITY",
            "display_name": community_bot_display_name(),
            "username": _preferred_bot_username(owner_id, "community"),
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
        if item.get("managed_externally"):
            lines.append(
                "  "
                + _msg(
                    "Recovery: wait for the DeathTG owner role service",
                    "Восстановление: дождитесь запуска сервиса ролей владельца DeathTG",
                )
            )
        elif role in {"inline", "helper", "community"}:
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


def _creation_needed_failures(status: dict) -> list[dict]:
    failures: list[dict] = []
    for item in list(status.get("bots") or []):
        if not isinstance(item, dict):
            continue
        configured = bool(item.get("configured"))
        valid_username = bool(item.get("valid_username"))
        error = str(item.get("error") or "")
        if not configured or not valid_username or "missing" in error.lower():
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
        creatable_failures = [item for item in failures if not item.get("managed_externally")]
        for item in failures:
            if item.get("managed_externally"):
                lines.append(
                    _msg(
                        "Role service is temporarily offline; wait for the DeathTG owner to reconnect.",
                        "Сервис ролей временно офлайн; дождитесь подключения владельца DeathTG.",
                    )
                )
                continue
            slot = {"inline": "1", "helper": "2", "community": "3"}.get(str(item.get("role") or ""))
            if not slot:
                continue
            guide = manual_bot_blueprint(owner_id, slot)
            if not guide:
                continue
            lines.append(f"{_slot_command_name(slot)} -> @{guide['username']}")
        if creatable_failures:
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


def _auto_repair_state(previous_status: dict | None) -> dict:
    previous_status = previous_status or {}
    state = previous_status.get("auto_repair", {})
    return state if isinstance(state, dict) else {}


def _auto_repair_allowed(previous_status: dict | None) -> tuple[bool, int]:
    state = _auto_repair_state(previous_status)
    last_attempt_at = int(state.get("last_attempt_at", 0) or 0)
    now = int(time.time())
    if not last_attempt_at or now - last_attempt_at >= AUTO_BOT_REPAIR_INTERVAL:
        return True, 0
    return False, AUTO_BOT_REPAIR_INTERVAL - (now - last_attempt_at)


async def _send_auto_repair_notice(client, owner_id: int, failures: list[dict]) -> None:
    labels = {"inline": "Inline", "helper": "Helper", "community": "Community"}
    bot_list = ", ".join(labels.get(str(item.get("role") or ""), "Bot") for item in failures) or "Bots"
    text = _msg(
        f"DeathTG is trying to recreate missing bots automatically via BotFather.\n\nTargets: {bot_list}\n\nIf Telegram rate-limits BotFather, DeathTG will keep the manual recovery commands as a fallback.",
        f"DeathTG пытается автоматически пересоздать отсутствующих ботов через BotFather.\n\nЦели: {bot_list}\n\nЕсли Telegram ограничит BotFather, DeathTG оставит ручные команды восстановления как запасной вариант.",
    )
    await _send_saved_message(client, text)


async def _attempt_missing_bot_repair(client, owner_id: int, status: dict, previous_status: dict | None = None) -> dict | None:
    failures = _creation_needed_failures(status)
    if not failures:
        return None
    allowed, wait_left = _auto_repair_allowed(previous_status)
    if not allowed:
        status["auto_repair"] = {
            "last_attempt_at": int(_auto_repair_state(previous_status).get("last_attempt_at", 0) or 0),
            "last_result": f"cooldown ({wait_left}s left)",
            "healthy": False,
        }
        return None
    await _send_auto_repair_notice(client, owner_id, failures)
    try:
        repaired = await run_startup_sync(client)
    except Exception as exc:
        status["auto_repair"] = {
            "last_attempt_at": int(time.time()),
            "last_result": str(exc),
            "healthy": False,
        }
        _write_status(status)
        return status
    repaired["auto_repair"] = {
        "last_attempt_at": int(time.time()),
        "last_result": "ok" if not _creation_needed_failures(repaired) else "partial",
        "healthy": not _creation_needed_failures(repaired),
    }
    _write_status(repaired)
    return repaired


async def _botfather_step(conv, message: str, *, retries: int = 4):
    last_response = None
    for _ in range(max(1, retries)):
        await conv.send_message(message)
        response = await conv.get_response()
        last_response = response
        raw_text = getattr(response, "raw_text", "") or ""
        wait_seconds = _botfather_retry_seconds(raw_text)
        if wait_seconds:
            _set_botfather_cooldown(wait_seconds, raw_text[:240])
            if wait_seconds > 30:
                return response
            await asyncio.sleep(min(wait_seconds + 1, 6))
            continue
        if message == "/newbot":
            _clear_botfather_cooldown()
        return response
    return last_response


async def _recover_bot_token_with_botfather(client, bot_username: str) -> tuple[str, str | None]:
    bot_username = str(bot_username or "").lstrip("@").strip()
    if not bot_username:
        return "", "missing bot username"
    cooldown_error = _botfather_cooldown_error()
    if cooldown_error:
        return "", cooldown_error
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass
    try:
        async with _BOTFATHER_LOCK:
            async with _botfather_process_guard(f"recover @{bot_username}") as (allowed, guard_error):
                if not allowed:
                    return "", guard_error
                async with client.conversation("BotFather", timeout=90, exclusive=True) as conv:
                    with contextlib.suppress(Exception):
                        await _botfather_step(conv, "/cancel", retries=1)
                    first = await _botfather_step(conv, "/token")
                    first_text = getattr(first, "raw_text", "") or ""
                    if _botfather_retry_seconds(first_text):
                        return "", first_text[:240]
                    if "choose a bot" not in first_text.lower() and "select a bot" not in first_text.lower():
                        # BotFather can still accept the username after a generic help message.
                        if "create and manage telegram bots" in first_text.lower():
                            first = await _botfather_step(conv, "/token")
                            first_text = getattr(first, "raw_text", "") or ""
                            if _botfather_retry_seconds(first_text):
                                return "", first_text[:240]
                    response = await _botfather_step(conv, f"@{bot_username}")
                    text = getattr(response, "raw_text", "") or ""
                    match = BOT_TOKEN_RE.search(text)
                    if match:
                        _clear_botfather_cooldown()
                        return match.group(0), None
                    if _botfather_retry_seconds(text):
                        return "", text[:240]
                    return "", text[:240] or "BotFather did not return a token"
    except Exception as exc:
        network_error = _botfather_network_error(exc)
        return "", network_error or str(exc)


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


async def _fetch_bot_username(bot_token: str, *, env_key: str = "BOT_TOKEN") -> tuple[str, str | None]:
    if not bot_token:
        return "", f"{env_key} is missing"
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
    cooldown_error = _botfather_cooldown_error()
    if cooldown_error:
        return "", cooldown_error
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass
    try:
        async with _BOTFATHER_LOCK:
            async with _botfather_process_guard(f"create {role} bot") as (allowed, guard_error):
                if not allowed:
                    return "", guard_error
                async with client.conversation("BotFather", timeout=120, exclusive=True) as conv:
                    with contextlib.suppress(Exception):
                        await _botfather_step(conv, "/cancel", retries=1)
                    first = await _botfather_step(conv, "/newbot")
                    first_text = getattr(first, "raw_text", "") or ""
                    if _botfather_retry_seconds(first_text):
                        return "", first_text[:240]
                    if "create and manage telegram bots" in first_text.lower():
                        first = await _botfather_step(conv, "/newbot")
                        first_text = getattr(first, "raw_text", "") or ""
                        if _botfather_retry_seconds(first_text):
                            return "", first_text[:240]
                    display_name = {
                        "inline": "DeathTG Inline",
                        "helper": "DeathTG Helper",
                        "community": community_bot_display_name(),
                    }.get(role, "DeathTG Inline")
                    print(f"BotFather: creating {role} bot for owner {owner_id}")
                    name_response = await _botfather_step(conv, display_name)
                    name_text = getattr(name_response, "raw_text", "") or ""
                    lowered_name = name_text.lower()
                    if "choose a username" not in lowered_name and "must end in" not in lowered_name:
                        return "", name_text[:240] or "BotFather did not ask for a username"
                    candidates = [_preferred_bot_username(owner_id, role)]
                    candidates.extend(_random_bot_username(owner_id, role) for _ in range(19))
                    for candidate in candidates:
                        response = await _botfather_step(conv, candidate)
                        text = getattr(response, "raw_text", "") or ""
                        match = BOT_TOKEN_RE.search(text)
                        if match:
                            _clear_botfather_cooldown()
                            update_env_value(_bot_username_env_key(role), candidate)
                            return match.group(0), None
                        if _botfather_retry_seconds(text):
                            return "", text[:240]
                        lower = text.lower()
                        if "create and manage telegram bots" in lower:
                            return "", text[:240] or "BotFather left the /newbot flow"
                        if all(word not in lower for word in ("taken", "sorry", "username", "invalid")):
                            return "", text[:240]
    except Exception as exc:
        network_error = _botfather_network_error(exc)
        return "", network_error or str(exc)
    return "", "BotFather did not return a token"


async def _create_named_bot_with_botfather(client, display_name: str, username: str) -> tuple[str, str | None]:
    cooldown_error = _botfather_cooldown_error()
    if cooldown_error:
        return "", cooldown_error
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass
    try:
        async with _BOTFATHER_LOCK:
            async with _botfather_process_guard(f"create @{username}") as (allowed, guard_error):
                if not allowed:
                    return "", guard_error
                async with client.conversation("BotFather", timeout=120, exclusive=True) as conv:
                    with contextlib.suppress(Exception):
                        await _botfather_step(conv, "/cancel", retries=1)
                    first = await _botfather_step(conv, "/newbot")
                    first_text = getattr(first, "raw_text", "") or ""
                    if _botfather_retry_seconds(first_text):
                        return "", first_text[:240]
                    if "create and manage telegram bots" in first_text.lower():
                        first = await _botfather_step(conv, "/newbot")
                        first_text = getattr(first, "raw_text", "") or ""
                        if _botfather_retry_seconds(first_text):
                            return "", first_text[:240]
                    print(f"BotFather: creating named bot @{username}")
                    name_response = await _botfather_step(conv, display_name)
                    name_text = getattr(name_response, "raw_text", "") or ""
                    lowered_name = name_text.lower()
                    if "choose a username" not in lowered_name and "must end in" not in lowered_name:
                        return "", name_text[:240] or "BotFather did not ask for a username"
                    response = await _botfather_step(conv, username)
                    text = getattr(response, "raw_text", "") or ""
                    match = BOT_TOKEN_RE.search(text)
                    if match:
                        _clear_botfather_cooldown()
                        return match.group(0), None
                    if _botfather_retry_seconds(text):
                        return "", text[:240]
                    return "", text[:240] or "BotFather did not return a token"
    except Exception as exc:
        network_error = _botfather_network_error(exc)
        return "", network_error or str(exc)


async def _set_bot_profile(bot_token: str, owner_id: int, role: str = "inline") -> tuple[bool, str | None]:
    if not bot_token:
        return False, "missing bot token"
    base = f"https://api.telegram.org/bot{bot_token}"
    role_title = role if role in {"helper", "community"} else "inline"
    if role == "community":
        command_items = [
            {"command": "start", "description": "DeathTG role service"},
            {"command": "redeem", "description": "Activate a one-time role key"},
            {"command": "userinfo", "description": "Owner: list role holders"},
            {"command": "aduseradm", "description": "Owner: create Admin key"},
            {"command": "aduserdev", "description": "Owner: create Developer key"},
            {"command": "deluseradm", "description": "Owner: revoke Admin by ID"},
            {"command": "deluserdev", "description": "Owner: revoke Developer by ID"},
        ]
    else:
        command_items = [
            {"command": "start", "description": f"DeathTG {role_title} bot"},
            {"command": "status", "description": "Runtime status"},
        ]
    commands = {"commands": command_items}
    descriptions = {
        "inline": "DeathTG inline control plane",
        "helper": "DeathTG helper delivery service",
        "community": "DeathTG role verification service",
    }
    description = {"description": descriptions.get(role, "DeathTG control service")}
    short_description = {"short_description": f"DeathTG {role_title} runtime"}
    display_name = {
        "inline": "DeathTG Inline",
        "helper": "DeathTG Helper",
        "community": community_bot_display_name(),
    }.get(role, "DeathTG Inline")
    try:
        async with aiohttp.ClientSession() as session:
            for method, payload in (
                ("setMyName", {"name": display_name}),
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
    cooldown_error = _botfather_cooldown_error()
    if cooldown_error:
        return False, cooldown_error
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass
    try:
        async with _BOTFATHER_LOCK:
            async with _botfather_process_guard(f"sync avatar @{bot_username}", wait_seconds=8) as (allowed, guard_error):
                if not allowed:
                    return False, guard_error
                async with client.conversation("BotFather", timeout=120, exclusive=True) as conv:
                    with contextlib.suppress(Exception):
                        await _botfather_step(conv, "/cancel", retries=1)
                    first = await _botfather_step(conv, "/setuserpic")
                    first_text = getattr(first, "raw_text", "") or ""
                    if _botfather_retry_seconds(first_text):
                        return False, first_text[:240]
                    second = await _botfather_step(conv, f"@{bot_username}")
                    second_text = getattr(second, "raw_text", "") or ""
                    if _botfather_retry_seconds(second_text):
                        return False, second_text[:240]
                    await conv.send_file(str(BOT_AVATAR))
                    with contextlib.suppress(Exception):
                        await conv.get_response()
    except Exception as exc:
        return False, str(exc)
    return True, None


async def _ensure_bot_inline(client, bot_username: str) -> tuple[bool, str | None]:
    if not bot_username:
        return False, "missing bot username"
    cooldown_error = _botfather_cooldown_error()
    if cooldown_error:
        return False, cooldown_error
    try:
        botfather = await client.get_input_entity("BotFather")
        await client(UnblockRequest(botfather))
    except Exception:
        pass

    try:
        async with _BOTFATHER_LOCK:
            async with _botfather_process_guard(f"sync inline @{bot_username}", wait_seconds=8) as (allowed, guard_error):
                if not allowed:
                    return False, guard_error
                async with client.conversation("BotFather", timeout=120, exclusive=True) as conv:
                    with contextlib.suppress(Exception):
                        await _botfather_step(conv, "/cancel", retries=1)

                    first = await _botfather_step(conv, "/setinline")
                    first_text_raw = getattr(first, "raw_text", "") or ""
                    if _botfather_retry_seconds(first_text_raw):
                        return False, first_text_raw[:240]
                    first_text = first_text_raw.lower()
                    if "choose a bot" not in first_text and "select a bot" not in first_text and "@" not in first_text:
                        return False, first_text_raw[:240] or "BotFather did not ask for a bot"

                    second = await _botfather_step(conv, f"@{bot_username}")
                    second_text_raw = getattr(second, "raw_text", "") or ""
                    if _botfather_retry_seconds(second_text_raw):
                        return False, second_text_raw[:240]
                    second_text = second_text_raw.lower()
                    if any(word in second_text for word in ("placeholder", "input field", "inline")):
                        final = await _botfather_step(conv, "DeathTG")
                        final_text = getattr(final, "raw_text", "") or ""
                    else:
                        final_text = second_text_raw

                    lower = final_text.lower()
                    if any(word in lower for word in ("success", "enabled", "updated", "changed")):
                        return True, None
                    if "already" in lower and "inline" in lower:
                        return True, None
                    return False, final_text[:240] or "BotFather did not confirm inline mode"
    except Exception as exc:
        return False, str(exc)


async def _set_peer_archive_state(client, peer, *, archived: bool) -> tuple[bool, str | None]:
    if not peer:
        return False, "missing peer"
    try:
        # Bots stay inside the DeathTG dialog folder, but live in Telegram's
        # Archive so they do not clutter the main chat list.
        await client(EditPeerFoldersRequest(folder_peers=[InputFolderPeer(peer, 1 if archived else 0)]))
        return True, None
    except Exception as exc:
        return False, str(exc)


async def _bot_dialog_exists(client, username: str) -> bool:
    username_l = str(username or "").lstrip("@").strip().lower()
    if not username_l:
        return False
    async for dialog in client.iter_dialogs(ignore_pinned=False, archived=None):
        entity = getattr(dialog, "entity", None)
        if not entity or not getattr(entity, "bot", False):
            continue
        if str(getattr(entity, "username", "") or "").strip().lower() == username_l:
            return True
    return False


async def _ping_bot_runtime(client, username: str) -> tuple[bool, str | None]:
    if not username:
        return False, "missing bot username"
    try:
        if not await _bot_dialog_exists(client, username):
            await client.send_message(username, "/start")
    except Exception as exc:
        return False, str(exc)
    return True, None


async def check_runtime_integrity(client, *, notify: bool = True, allow_repair: bool = True) -> dict:
    me = await client.get_me()
    owner_id = int(getattr(me, "id", 0) or 0)
    previous_status = _load_status()
    bots: list[dict] = []
    for blueprint in manual_bot_blueprints(owner_id):
        if blueprint["role"] == "community" and not community_enabled_for_owner(owner_id):
            username = preferred_community_bot_username(owner_id)
            start_ping, start_ping_error = await _ping_bot_runtime(client, username)
            bots.append(
                {
                    "configured": bool(username),
                    "role": "community",
                    "env_key": "DEATHTG_ROLE_BOT_USERNAME",
                    "username": username,
                    "created": False,
                    "valid_username": bool(username),
                    "expected_prefix": username,
                    "owner_id": owner_id,
                    "commands_synced": None,
                    "inline_synced": None,
                    "avatar_synced": None,
                    "start_ping": start_ping,
                    "archived": None,
                    "managed_externally": True,
                    "error": start_ping_error,
                }
            )
            continue
        token = _env(blueprint["env_key"])
        username, token_error = await _fetch_bot_username(token, env_key=blueprint["env_key"])
        valid_username = _is_valid_bot_username(username, owner_id)
        if valid_username:
            update_env_value(_bot_username_env_key(str(blueprint["role"])), username)
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
    if allow_repair:
        repaired_status = await _attempt_missing_bot_repair(client, owner_id, runtime_status, previous_status)
        if repaired_status is not None:
            return repaired_status
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
    allow_create: bool = False,
) -> tuple[str, dict]:
    username, token_error = await _fetch_bot_username(bot_token, env_key=env_key)
    cooldown_error = _botfather_cooldown_error()
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

    if not allow_create:
        if not bot_token:
            status["error"] = f"{env_key} is missing"
        elif not status["error"]:
            status["error"] = "bot username does not match expected owner prefix"
        return bot_token, status
    if cooldown_error:
        status["error"] = cooldown_error
        return bot_token, status

    for candidate in await _discover_service_bot_usernames(client, owner_id, role):
        token, recover_error = await _recover_bot_token_with_botfather(client, candidate)
        if not token:
            status["error"] = recover_error or status.get("error") or f"unable to recover @{candidate}"
            continue
        recovered_username, recovered_error = await _fetch_bot_username(token, env_key=env_key)
        if _is_valid_bot_username(recovered_username, owner_id):
            update_env_value(env_key, token)
            update_env_value(_bot_username_env_key(role), recovered_username)
            status.update(
                {
                    "configured": True,
                    "username": recovered_username,
                    "created": False,
                    "recovered": True,
                    "valid_username": True,
                    "error": None,
                }
            )
            return token, status
        status["error"] = recovered_error or f"recovered @{candidate}, but username validation failed"

    try:
        token, error = await asyncio.wait_for(
            _create_bot_with_botfather(client, owner_id, role),
            timeout=BOTFATHER_CREATE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        status["error"] = "BotFather creation timed out"
        return bot_token, status
    if not token:
        status["error"] = error or token_error or "unable to create owner-bound bot"
        return bot_token, status

    update_env_value(env_key, token)
    username, token_error = await _fetch_bot_username(token, env_key=env_key)
    if _is_valid_bot_username(username, owner_id):
        update_env_value(_bot_username_env_key(role), username)
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


async def _ensure_community_bot(client, owner_id: int, *, allow_create: bool = False) -> tuple[str, dict]:
    bot_token = _env("BOT_TOKEN_COMMUNITY")
    username, token_error = await _fetch_bot_username(bot_token, env_key="BOT_TOKEN_COMMUNITY")
    cooldown_error = _botfather_cooldown_error()
    status = {
        "configured": bool(bot_token),
        "role": "community",
        "env_key": "BOT_TOKEN_COMMUNITY",
        "username": username,
        "created": False,
        "valid_username": _is_valid_bot_username(username, owner_id),
        "expected_prefix": _expected_prefix(owner_id),
        "owner_id": owner_id,
        "error": token_error,
    }
    if status["valid_username"]:
        update_env_value("COMMUNITY_BOT_USERNAME", username)
        update_env_value("DEATHTG_ROLE_BOT_USERNAME", username)
        status["error"] = None
        return bot_token, status
    if not allow_create:
        if not bot_token:
            status["error"] = "BOT_TOKEN_COMMUNITY is missing"
        elif not status["error"]:
            status["error"] = "community bot username must match the owner-bound pattern"
        return bot_token, status
    if cooldown_error:
        status["error"] = cooldown_error
        return bot_token, status
    for candidate in await _discover_service_bot_usernames(client, owner_id, "community"):
        token, recover_error = await _recover_bot_token_with_botfather(client, candidate)
        if not token:
            status["error"] = recover_error or status.get("error") or f"unable to recover @{candidate}"
            continue
        recovered_username, recovered_error = await _fetch_bot_username(token, env_key="BOT_TOKEN_COMMUNITY")
        if _is_valid_bot_username(recovered_username, owner_id):
            update_env_value("BOT_TOKEN_COMMUNITY", token)
            update_env_value("COMMUNITY_BOT_USERNAME", recovered_username)
            update_env_value("DEATHTG_ROLE_BOT_USERNAME", recovered_username)
            status.update(
                {
                    "configured": True,
                    "username": recovered_username,
                    "created": False,
                    "recovered": True,
                    "valid_username": True,
                    "error": None,
                }
            )
            return token, status
        status["error"] = recovered_error or f"recovered @{candidate}, but username validation failed"
    try:
        token, error = await asyncio.wait_for(
            _create_bot_with_botfather(client, owner_id, "community"),
            timeout=BOTFATHER_CREATE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        status["error"] = "BotFather creation timed out"
        return bot_token, status
    if not token:
        status["error"] = error or token_error or "unable to create community bot"
        return bot_token, status
    update_env_value("BOT_TOKEN_COMMUNITY", token)
    username, token_error = await _fetch_bot_username(token, env_key="BOT_TOKEN_COMMUNITY")
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
        status["error"] = "community bot username must match the owner-bound pattern"
    if status["valid_username"]:
        update_env_value("COMMUNITY_BOT_USERNAME", username)
        update_env_value("DEATHTG_ROLE_BOT_USERNAME", username)
    return token, status


async def run_startup_sync(client) -> dict:
    async with _STARTUP_SYNC_LOCK:
        return await _run_startup_sync_locked(client)


async def _run_startup_sync_locked(client) -> dict:
    me = await client.get_me()
    owner_id = int(getattr(me, "id", 0) or 0)
    update_env_value("OWNER_ID", str(owner_id))
    if not community_enabled_for_owner(owner_id):
        update_env_value("COMMUNITY_BOT_USERNAME", preferred_community_bot_username(owner_id))
    bot_token = _env("BOT_TOKEN")
    helper_token = _env("BOT_TOKEN_HELPER")
    community_token = _env("BOT_TOKEN_COMMUNITY")

    bot_token, bot_status = await _ensure_bot(
        client,
        bot_token,
        owner_id,
        env_key="BOT_TOKEN",
        role="inline",
        allow_create=True,
    )
    helper_token, helper_status = await _ensure_bot(
        client,
        helper_token,
        owner_id,
        env_key="BOT_TOKEN_HELPER",
        role="helper",
        allow_create=True,
    )
    central_role_service = not community_enabled_for_owner(owner_id)
    community_status = {
        "configured": bool(community_token) if not central_role_service else bool(_env("COMMUNITY_BOT_USERNAME")),
        "role": "community",
        "env_key": "BOT_TOKEN_COMMUNITY",
        "username": _env("COMMUNITY_BOT_USERNAME"),
        "created": False,
        "valid_username": bool(_env("COMMUNITY_BOT_USERNAME")) if central_role_service else False,
        "expected_prefix": _expected_prefix(owner_id),
        "owner_id": owner_id,
        "error": None if central_role_service else "Community bot is owner-only",
        "managed_externally": central_role_service,
        "commands_synced": False,
        "inline_synced": False,
        "avatar_synced": False,
        "start_ping": False,
        "archived": False,
    }
    if community_enabled_for_owner(owner_id):
        community_token, community_status = await _ensure_community_bot(client, owner_id, allow_create=True)
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
    if community_username:
        community_start_ping, community_start_ping_error = await _ping_bot_runtime(client, community_username)
    bot_status["start_ping"] = start_ping
    helper_status["start_ping"] = helper_start_ping
    community_status["start_ping"] = community_start_ping

    bot_status["archived"] = False
    helper_status["archived"] = False
    community_status["archived"] = False

    if not bot_status.get("error"):
        bot_status["error"] = _collect_error(commands_error, inline_error, avatar_error, start_ping_error)
    if not helper_status.get("error"):
        helper_status["error"] = _collect_error(
            helper_commands_error,
            helper_avatar_error,
            helper_start_ping_error,
        )
    if not community_status.get("error"):
        community_status["error"] = _collect_error(
            community_commands_error,
            community_avatar_error,
            community_start_ping_error,
        )

    status = {
        "bot": bot_status,
        "helper_bot": helper_status,
        "community_bot": community_status,
        "bots": [bot_status, helper_status, community_status],
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
    folder_seen: set[tuple] = set()
    include_usernames: list[str] = []
    include_seen: set[str] = set()
    foldered_roles = {"inline": False, "helper": False, "community": False}

    async def _add_folder_peer(peer, label: str, *, archive: bool = False) -> None:
        key = _peer_key(peer)
        if key not in folder_seen:
            folder_seen.add(key)
            folder_peers.append(peer)
            ok, error = await _set_peer_archive_state(client, peer, archived=archive)
            if not ok and error and not status["last_sync_error"]:
                status["last_sync_error"] = f"{label}: {error}"
        clean_label = str(label or "").strip()
        clean_key = clean_label.lower()
        if clean_label and clean_key not in include_seen:
            include_seen.add(clean_key)
            include_usernames.append(clean_label)

    for role, username in (
        ("inline", bot_username),
        ("helper", helper_username),
        ("community", community_username),
    ):
        if not username:
            continue
        try:
            bot_entity = await client.get_entity(username)
            bot_peer = await client.get_input_entity(bot_entity)
            with contextlib.suppress(Exception):
                await client(UnblockRequest(bot_peer))
            await _add_folder_peer(bot_peer, f"@{username}", archive=True)
            foldered_roles[role] = True
            if role == "inline":
                bot_status["archived"] = True
            elif role == "helper":
                helper_status["archived"] = True
            elif role == "community":
                community_status["archived"] = True
        except Exception as exc:
            if not status["last_sync_error"]:
                status["last_sync_error"] = str(exc)

    try:
        discovered_peers, discovered_usernames = await _discover_service_bot_peers(
            client,
            owner_id,
            [bot_username, helper_username, community_username],
        )
        for peer, label in zip(discovered_peers, discovered_usernames):
            await _add_folder_peer(peer, label, archive=True)
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
            await _add_folder_peer(channel_peer, f"@{channel_name}")
            row["joined"] = True
        except Exception as exc:
            row["error"] = str(exc)
        status["channels"].append(row)

    folder_ok, folder_error = await _ensure_folder(client, folder_peers)
    status["folder"]["ok"] = folder_ok
    status["folder"]["error"] = folder_error
    status["folder"]["include_count"] = len(folder_peers)
    status["folder"]["include_usernames"] = include_usernames
    bot_status["foldered"] = bool(folder_ok and foldered_roles["inline"])
    helper_status["foldered"] = bool(folder_ok and foldered_roles["helper"])
    community_status["foldered"] = bool(folder_ok and foldered_roles["community"])

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
        me = await client.get_me()
        owner_premium = bool(getattr(me, "premium", False))
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
                    emoji_line("pirate", "Welcome to DeathTG.", owner_premium) + "\n\n",
                    emoji_line("mail", "Your personal private panel link is ready.", owner_premium) + "\n",
                    emoji_line("key", "Do not share this link with anyone.", owner_premium) + "\n",
                    emoji_line("phone", "Open this link on your phone or on another browser if you want to trust one more device.", owner_premium) + "\n",
                    "If you need another device later, create a new secure link from inside the panel.",
                    remote_note,
                ]
            ),
            "reply_markup": {"inline_keyboard": buttons},
            "disable_web_page_preview": True,
            "parse_mode": "HTML",
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
                            form_data.add_field("parse_mode", "HTML")
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
                emoji_line("pirate", "Welcome to DeathTG.", owner_premium),
                "",
                emoji_line("mail", "Your personal private panel link is ready.", owner_premium),
                emoji_line("key", "Do not share this link with anyone.", owner_premium),
                panel_url,
            ]
            if remote_note:
                direct_lines.extend(["", remote_note.strip()])
            await client.send_message("me", "\n".join(direct_lines), parse_mode="html", link_preview=False)
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
