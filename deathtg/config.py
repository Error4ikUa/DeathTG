import os
from dataclasses import dataclass
from pathlib import Path

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


def load_config() -> DeathTGConfig:
    load_dotenv(ENV_PATH)

    api_id_raw = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    session_name = os.getenv("SESSION_NAME", "deathtg").strip() or "deathtg"
    prefix = os.getenv("COMMAND_PREFIX", ".").strip() or "."
    owner_raw = os.getenv("OWNER_ID", "").strip()

    if not api_id_raw or not api_hash:
        raise RuntimeError(
            "API_ID/API_HASH are missing. Create .env from .env.example and add values from my.telegram.org"
        )

    owner_id = int(owner_raw) if owner_raw else None

    return DeathTGConfig(
        api_id=int(api_id_raw),
        api_hash=api_hash,
        session_name=session_name,
        command_prefix=prefix,
        owner_id=owner_id,
    )
