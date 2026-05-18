from __future__ import annotations

import json
import os
from pathlib import Path

from deathtg.config import ROOT_DIR, RUNTIME_DIR

PROFILE_SETTINGS_PATH = RUNTIME_DIR / "profile_settings.json"
DEFAULT_PROFILE_SETTINGS = {
    "language": "en",
    "description": "",
    "accent": "blue",
    "profile_title": "DeathTG Operator",
    "role": "user",
    "info_text": "",
    "backup_enabled": "0",
    "backup_interval_hours": "24",
    "onboarding_done": "0",
}

def profile_settings() -> dict[str, str]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(DEFAULT_PROFILE_SETTINGS)
    if PROFILE_SETTINGS_PATH.exists():
        try:
            raw = json.loads(PROFILE_SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: str(v) for k, v in raw.items() if v is not None})
        except Exception:
            pass
    if data.get("language") not in {"en", "ru"}:
        data["language"] = "en"
    if data.get("accent") not in {"blue", "red", "gold", "green", "purple", "dark"}:
        data["accent"] = "blue"
    if data.get("role") not in {"user", "admin", "developer"}:
        data["role"] = "user"
    return data

def save_profile_settings(**updates: str) -> dict[str, str]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    data = profile_settings()
    for key, value in updates.items():
        if value is not None:
            data[key] = str(value).strip()
    if data.get("language") not in {"en", "ru"}:
        data["language"] = "en"
    if data.get("accent") not in {"blue", "red", "gold", "green", "purple", "dark"}:
        data["accent"] = "blue"
    if data.get("role") not in {"user", "admin", "developer"}:
        data["role"] = "user"
    PROFILE_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

def update_env_value(key: str, value: str) -> None:
    env = ROOT_DIR / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    out: list[str] = []
    found = False
    
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
            
    if not found:
        out.append(f"{key}={value}")
        
    content = "\n".join(out).rstrip() + "\n"
    temp_path = env.with_suffix(".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, env)
    os.environ[key] = value
