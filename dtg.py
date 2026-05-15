from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

userbot_process = None

def cleanup(signum, frame):
    global userbot_process
    if userbot_process:
        userbot_process.terminate()
        userbot_process.wait()
    sys.exit(0)

def run_panel() -> None:
    host = os.getenv("PANEL_HOST", "127.0.0.1")
    port = int(os.getenv("PANEL_PORT", "8080"))
    uvicorn.run("deathtg.panel.clean_app:app", host=host, port=port)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    print("DeathTG full stack is starting...")
    print("Panel: http://127.0.0.1:8080")
    print("Userbot: starting in background process")
    
    userbot_process = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT_DIR)
    
    try:
        run_panel()
    finally:
        if userbot_process:
            userbot_process.terminate()
            userbot_process.wait()
