from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from deathtg.panel_access import effective_panel_bind_host, panel_base_url
from deathtg.server_bootstrap import ensure_server_env
from deathtg.setup_access import setup_link

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

userbot_process = None
supervisor_stop = threading.Event()
last_start_attempt = 0.0
MIN_RESTART_INTERVAL = 5.0


def running_in_termux() -> bool:
    prefix = os.getenv("PREFIX", "")
    return "com.termux" in prefix.lower() or bool(os.getenv("TERMUX_VERSION"))


def stop_userbot(timeout: float = 8.0) -> None:
    global userbot_process
    process = userbot_process
    if process is None:
        return
    if process.poll() is not None:
        userbot_process = None
        return
    try:
        process.terminate()
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)
    except ProcessLookupError:
        pass
    finally:
        userbot_process = None


def _session_path_from_env() -> Path:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
    session_name = os.getenv("SESSION_NAME", "deathtg").strip() or "deathtg"
    return ROOT_DIR / f"{session_name}.session"


def _userbot_ready() -> bool:
    if not ENV_PATH.exists():
        return False
    load_dotenv(ENV_PATH, override=True)
    login_pending = (os.getenv("LOGIN_PENDING", "0").strip().lower() or "0") in {"1", "true", "yes", "on"}
    if login_pending:
        return False
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    if not api_id or not api_hash:
        return False
    return _session_path_from_env().exists()


def ensure_userbot_running() -> None:
    global userbot_process, last_start_attempt
    now = time.time()
    process = userbot_process
    if process is not None and process.poll() is None:
        return
    if not _userbot_ready():
        return
    if now - last_start_attempt < MIN_RESTART_INTERVAL:
        return
    last_start_attempt = now
    userbot_process = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT_DIR)
    print("Userbot: started")


def supervisor_loop() -> None:
    while not supervisor_stop.is_set():
        try:
            ensure_userbot_running()
        except Exception as exc:
            print(f"Userbot supervisor warning: {type(exc).__name__}: {exc}")
        supervisor_stop.wait(2.0)


def cleanup(signum, frame):
    supervisor_stop.set()
    stop_userbot()
    sys.exit(0)


def panel_url() -> str:
    return panel_base_url()


def run_panel() -> None:
    host = effective_panel_bind_host()
    port = int(os.getenv("PANEL_PORT", "8080"))
    uvicorn.run("deathtg.panel.clean_app:app", host=host, port=port, log_level="warning", access_log=False)

if __name__ == "__main__":
    if running_in_termux():
        print("DeathTG does not support Termux.")
        print("Use a normal Linux server, VPS, or desktop Python environment instead.")
        sys.exit(1)
    ensure_server_env()
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    print("DeathTG full stack is starting...")
    print(f"Panel address: {panel_url()}")
    if not _userbot_ready():
        print(f"First run setup link: {setup_link()}")
    print("First run: open setup, enter API_ID/API_HASH, scan the QR code in Telegram, then enter 2FA only if Telegram asks for it.")
    print("Console never asks for the Telegram code. DeathTG waits for QR approval from the website flow and finishes login in the background.")
    if not os.getenv("PANEL_PUBLIC_URL", "").strip():
        print("HTTPS is not enabled yet. For a real public site with a certificate, set a domain and PANEL_PUBLIC_URL.")
    print("Userbot: will auto-start after setup and session creation.")
    print("Git updates are not auto-applied. DeathTG will notify you in Telegram when a new update appears.")
    supervisor_thread = threading.Thread(target=supervisor_loop, name="dtg-userbot-supervisor", daemon=True)
    supervisor_thread.start()
    try:
        run_panel()
    finally:
        supervisor_stop.set()
        supervisor_thread.join(timeout=2.0)
        stop_userbot()
