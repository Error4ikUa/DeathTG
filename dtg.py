from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)


def run_userbot() -> None:
    subprocess.run([sys.executable, "main.py"], cwd=ROOT_DIR)


def run_panel() -> None:
    host = os.getenv("PANEL_HOST", "127.0.0.1")
    port = int(os.getenv("PANEL_PORT", "8080"))
    uvicorn.run("deathtg.panel.clean_app:app", host=host, port=port)


if __name__ == "__main__":
    print("DeathTG full stack is starting...")
    print("Panel: http://127.0.0.1:8080")
    print("Userbot: starting in background thread")
    thread = threading.Thread(target=run_userbot, daemon=True)
    thread.start()
    run_panel()
