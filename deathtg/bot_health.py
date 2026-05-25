from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from deathtg.config import ENV_PATH, ROOT_DIR, RUNTIME_DIR
from deathtg.server_bootstrap import parse_env_file
from deathtg.session_guard import session_main_file
from deathtg.state_db import set_health, upsert, event


@dataclass(slots=True)
class BotCheck:
    bot_key: str
    label: str
    required: bool
    token_env: str
    token_present: bool
    session_present: bool
    status: str
    username: str = ""
    bot_id: str = ""
    error: str = ""
    recovery: str = ""


BOT_BLUEPRINTS = (
    {
        "bot_key": "userbot",
        "label": "Userbot",
        "required": True,
        "token_env": "",
        "session_file": "user-session",
        "recovery": "Open setup page and finish Telegram login.",
    },
    {
        "bot_key": "inline",
        "label": "Inline bot",
        "required": False,
        "token_env": "BOT_TOKEN",
        "session_file": "runtime/inline_bot.session",
        "recovery": "Create bot in BotFather and save token to BOT_TOKEN.",
    },
    {
        "bot_key": "helper",
        "label": "Helper bot",
        "required": False,
        "token_env": "BOT_TOKEN_HELPER",
        "session_file": "runtime/helper_bot.session",
        "recovery": "Create helper bot in BotFather and save token to BOT_TOKEN_HELPER.",
    },
    {
        "bot_key": "community",
        "label": "Community bot",
        "required": False,
        "token_env": "BOT_TOKEN_COMMUNITY",
        "session_file": "runtime/community_bot.session",
        "recovery": "Create community bot in BotFather and save token to BOT_TOKEN_COMMUNITY.",
    },
)


def _session_name(env: dict[str, str]) -> str:
    return (env.get("SESSION_NAME") or os.getenv("SESSION_NAME", "deathtg")).strip() or "deathtg"


def _session_present(raw_path: str, env: dict[str, str]) -> bool:
    if raw_path == "user-session":
        return session_main_file(_session_name(env)).exists()
    path = ROOT_DIR / raw_path
    return path.exists() or any(path.parent.glob(path.name + "*"))


def _token_present(env_name: str, env: dict[str, str]) -> bool:
    if not env_name:
        return bool((env.get("API_ID") or "").strip() and (env.get("API_HASH") or "").strip())
    return bool((env.get(env_name) or os.getenv(env_name, "")).strip())


def collect_bot_checks() -> list[BotCheck]:
    load_dotenv(ENV_PATH, override=True)
    env = parse_env_file(ENV_PATH)
    checks: list[BotCheck] = []
    for item in BOT_BLUEPRINTS:
        token_present = _token_present(str(item["token_env"]), env)
        session_present = _session_present(str(item["session_file"]), env)
        if item["bot_key"] == "userbot":
            login_pending = (env.get("LOGIN_PENDING") or os.getenv("LOGIN_PENDING", "0")).strip().lower() in {"1", "true", "yes", "on"}
            if not token_present:
                status = "missing_config"
                error = "API_ID/API_HASH are missing"
            elif login_pending:
                status = "login_pending"
                error = "Telegram login is not finished"
            elif not session_present:
                status = "missing_session"
                error = "User session file is missing"
            else:
                status = "configured"
                error = ""
        else:
            if not token_present:
                status = "not_configured"
                error = "token is not configured"
            elif not session_present:
                status = "configured"
                error = "session will be created on next runtime start"
            else:
                status = "configured"
                error = ""
        checks.append(
            BotCheck(
                bot_key=str(item["bot_key"]),
                label=str(item["label"]),
                required=bool(item["required"]),
                token_env=str(item["token_env"]),
                token_present=token_present,
                session_present=session_present,
                status=status,
                error=error,
                recovery=str(item["recovery"]),
            )
        )
    return checks


def sync_bot_checks() -> list[BotCheck]:
    checks = collect_bot_checks()
    for check in checks:
        upsert(
            "bots",
            "bot_key",
            check.bot_key,
            {
                "username": check.username,
                "bot_id": check.bot_id,
                "token_present": 1 if check.token_present else 0,
                "status": check.status,
                "error": check.error,
                "last_checked": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            },
            preserve_existing=True,
            event_type="bot.check",
        )
    ok = all(c.status in {"configured", "not_configured"} for c in checks if not c.required) and all(c.status == "configured" for c in checks if c.required)
    set_health("bots", "ok" if ok else "warning", "Bot configuration check", [asdict(c) for c in checks])
    return checks


def record_runtime_bot(
    bot_key: str,
    *,
    username: str = "",
    bot_id: str = "",
    status: str = "ok",
    error: str = "",
    token_present: bool | None = None,
) -> None:
    data: dict[str, Any] = {
        "username": username.lstrip("@") if username else "",
        "bot_id": str(bot_id or ""),
        "status": status,
        "error": error,
        "last_checked": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    if token_present is not None:
        data["token_present"] = 1 if token_present else 0
    upsert("bots", "bot_key", bot_key, data, preserve_existing=True, event_type="bot.runtime")
    level = "ok" if status == "ok" else "warning"
    set_health(f"bot.{bot_key}", level, error or f"{bot_key} status: {status}", data)


def render_bot_summary(checks: list[BotCheck] | None = None) -> str:
    checks = checks or collect_bot_checks()
    lines = []
    for c in checks:
        icon = "OK" if c.status == "configured" else ("MISS" if c.status in {"not_configured", "missing_config", "missing_session"} else "WARN")
        lines.append(f"{icon} {c.label}: {c.status}" + (f" ({c.error})" if c.error else ""))
    return "\n".join(lines)
