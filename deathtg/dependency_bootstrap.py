from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"

CORE_IMPORTS = (
    "aiohttp",
    "aiosqlite",
    "dotenv",
    "fastapi",
    "itsdangerous",
    "jinja2",
    "multipart",
    "PIL",
    "psutil",
    "qrcode",
    "starlette",
    "telethon",
    "uvicorn",
    "yt_dlp",
)


def missing_core_imports() -> list[str]:
    missing: list[str] = []
    for module_name in CORE_IMPORTS:
        if importlib.util.find_spec(module_name) is None:
            missing.append(module_name)
    return missing


def ensure_core_dependencies() -> None:
    """Install project requirements before heavy imports can crash startup."""
    if os.getenv("DTG_SKIP_AUTO_DEPS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    missing = missing_core_imports()
    if not missing:
        return
    if not REQUIREMENTS_FILE.exists():
        raise RuntimeError(f"Missing Python packages: {', '.join(missing)}; requirements.txt was not found")

    print(f"DeathTG: installing missing Python packages: {', '.join(missing)}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
        cwd=str(ROOT_DIR),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        output = (result.stdout or "").strip()
        if output:
            print(output[-4000:])
        print("DeathTG could not install Python dependencies automatically.")
        print(f"Run manually: {sys.executable} -m pip install -r {REQUIREMENTS_FILE}")
        raise SystemExit(result.returncode)

    still_missing = missing_core_imports()
    if still_missing:
        print(f"DeathTG dependencies are still missing after install: {', '.join(still_missing)}")
        print(f"Run manually: {sys.executable} -m pip install -r {REQUIREMENTS_FILE}")
        raise SystemExit(1)
