from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT_DIR / "modules"
DOWNLOADS_DIR = ROOT_DIR / "downloads"
RUNTIME_DIR = ROOT_DIR / "runtime"
ENV_PATH = ROOT_DIR / ".env"


@dataclass(slots=True)
class DeathTGConfig:
    api_id: int
    api_hash: str
    session_name: str = "deathtg"
    command_prefix: str = "."
    owner_id: int | None = None
    safe_mode: bool = False


def load_config() -> DeathTGConfig:
    load_dotenv(ENV_PATH, override=True)
    from deathtg.config_manager import load_managed_config, sync_config

    sync_config(repair=False)
    managed = load_managed_config()

    api_id_raw = str(managed.get("api_id") or os.getenv("API_ID", "")).strip()
    api_hash = str(managed.get("api_hash") or os.getenv("API_HASH", "")).strip()
    session_name = str(managed.get("session_name") or os.getenv("SESSION_NAME", "deathtg")).strip() or "deathtg"
    prefix = str(managed.get("command_prefix") or os.getenv("COMMAND_PREFIX", ".")).strip() or "."
    owner_raw = str(managed.get("owner_id") or os.getenv("OWNER_ID", "")).strip()
    safe_mode_raw = str(managed.get("safe_mode") or os.getenv("DTG_SAFE_MODE", "0")).strip().lower()

    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "API_ID/API_HASH are missing. Open the web setup page or fill .env with values from my.telegram.org"
        )

    owner_id = int(owner_raw) if owner_raw else None

    return DeathTGConfig(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        session_name=session_name,
        command_prefix=prefix,
        owner_id=owner_id,
        safe_mode=safe_mode_raw in {"1", "true", "yes", "on"},
    )
