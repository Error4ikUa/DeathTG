from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from deathtg.config import ENV_PATH, ROOT_DIR, RUNTIME_DIR
from deathtg.server_bootstrap import parse_env_file, update_env_values

CONFIG_VERSION = 1
CONFIG_PATH = ROOT_DIR / "config.json"
CONFIG_STATUS_PATH = RUNTIME_DIR / "config_status.json"
SECRETS = {"api_hash", "bot_token", "bot_token_helper", "panel_secret", "phone"}

ENV_TO_CONFIG = {
    "API_ID": "api_id",
    "API_HASH": "api_hash",
    "SESSION_NAME": "session_name",
    "COMMAND_PREFIX": "command_prefix",
    "OWNER_ID": "owner_id",
    "PHONE": "phone",
    "BOT_TOKEN": "bot_token",
    "BOT_TOKEN_HELPER": "bot_token_helper",
    "PANEL_HOST": "panel_host",
    "PANEL_PORT": "panel_port",
    "PANEL_PUBLIC_URL": "panel_public_url",
    "PANEL_SECRET": "panel_secret",
}

CONFIG_TO_ENV = {value: key for key, value in ENV_TO_CONFIG.items()}

DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "api_id": "",
    "api_hash": "",
    "session_name": "deathtg",
    "command_prefix": ".",
    "owner_id": "",
    "phone": "",
    "bot_token": "",
    "bot_token_helper": "",
    "panel_host": "127.0.0.1",
    "panel_port": "8080",
    "panel_public_url": "",
    "panel_secret": "",
}

REQUIRED_FIELDS = ("api_id", "api_hash")
RECOMMENDED_FIELDS = ("owner_id", "bot_token", "bot_token_helper")


@dataclass(slots=True)
class ConfigFieldStatus:
    key: str
    state: str
    message: str
    secret: bool = False


@dataclass(slots=True)
class ConfigStatus:
    ok: bool
    version: int
    path: str
    fields: list[ConfigFieldStatus]

    def save(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_STATUS_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(DEFAULT_CONFIG)
    for key, value in data.items():
        if key in DEFAULT_CONFIG:
            normalized[key] = "" if value is None else str(value).strip()
    normalized["version"] = CONFIG_VERSION
    if not normalized["session_name"]:
        normalized["session_name"] = "deathtg"
    if not normalized["command_prefix"]:
        normalized["command_prefix"] = "."
    if not normalized["panel_host"]:
        normalized["panel_host"] = "127.0.0.1"
    if not normalized["panel_port"]:
        normalized["panel_port"] = "8080"
    return normalized


def env_to_config(env: dict[str, str]) -> dict[str, Any]:
    data = dict(DEFAULT_CONFIG)
    for env_key, cfg_key in ENV_TO_CONFIG.items():
        if env_key in env and str(env[env_key]).strip():
            data[cfg_key] = str(env[env_key]).strip()
    data["version"] = CONFIG_VERSION
    return normalize_config(data)


def load_managed_config() -> dict[str, Any]:
    file_cfg = _read_json(CONFIG_PATH)
    env_cfg = env_to_config(parse_env_file(ENV_PATH))
    merged = dict(DEFAULT_CONFIG)
    merged.update(file_cfg)
    for key, value in env_cfg.items():
        if key == "version":
            continue
        if value not in (None, ""):
            merged[key] = value
    return normalize_config(merged)


def sync_config(*, repair: bool = False) -> ConfigStatus:
    env = parse_env_file(ENV_PATH)
    if not CONFIG_PATH.exists():
        cfg = env_to_config(env)
        _write_json(CONFIG_PATH, cfg)
    else:
        cfg = normalize_config(load_managed_config())
        if repair or _read_json(CONFIG_PATH) != cfg:
            _write_json(CONFIG_PATH, cfg)

    env_updates: dict[str, str] = {}
    for cfg_key, env_key in CONFIG_TO_ENV.items():
        value = str(cfg.get(cfg_key, "") or "")
        if env.get(env_key, "") != value:
            env_updates[env_key] = value
    if env_updates:
        update_env_values(env_updates, path=ENV_PATH)

    status = validate_config(cfg)
    status.save()
    return status


def validate_config(cfg: dict[str, Any] | None = None) -> ConfigStatus:
    cfg = normalize_config(cfg or load_managed_config())
    fields: list[ConfigFieldStatus] = []

    for key in REQUIRED_FIELDS:
        value = str(cfg.get(key, "") or "").strip()
        if not value:
            fields.append(ConfigFieldStatus(key, "missing", f"{key} is required", key in SECRETS))
        elif key == "api_id" and not value.isdigit():
            fields.append(ConfigFieldStatus(key, "invalid", "api_id must be numeric", False))
        else:
            fields.append(ConfigFieldStatus(key, "ok", "configured", key in SECRETS))

    for key in RECOMMENDED_FIELDS:
        value = str(cfg.get(key, "") or "").strip()
        if not value:
            fields.append(ConfigFieldStatus(key, "optional", f"{key} is not configured", key in SECRETS))
        else:
            fields.append(ConfigFieldStatus(key, "ok", "configured", key in SECRETS))

    panel_port = str(cfg.get("panel_port", "") or "")
    try:
        port = int(panel_port)
        if not 1 <= port <= 65535:
            raise ValueError
        fields.append(ConfigFieldStatus("panel_port", "ok", "configured"))
    except ValueError:
        fields.append(ConfigFieldStatus("panel_port", "invalid", "panel_port must be 1-65535"))

    fatal = any(field.state in {"missing", "invalid"} and field.key in (*REQUIRED_FIELDS, "panel_port") for field in fields)
    return ConfigStatus(ok=not fatal, version=CONFIG_VERSION, path=str(CONFIG_PATH), fields=fields)


def masked_config() -> dict[str, Any]:
    cfg = load_managed_config()
    result: dict[str, Any] = {}
    for key, value in cfg.items():
        if key in SECRETS and value:
            text = str(value)
            result[key] = text[:4] + "..." + text[-4:] if len(text) > 10 else "***"
        else:
            result[key] = value
    return result
