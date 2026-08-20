from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from deathtg.dependency_bootstrap import ensure_core_dependencies

ensure_core_dependencies()

import uvicorn
from dotenv import load_dotenv
from deathtg.panel_access import (
    effective_panel_bind_host,
    panel_base_url,
    panel_local_url,
    panel_remote_access_ready,
)
from deathtg.server_bootstrap import ensure_server_env, update_env_values
from deathtg.setup_access import setup_link
from deathtg.tailscale import ensure_tailscale_serve
from deathtg.startup_core import print_report, ready_to_start_userbot, run_preflight
from deathtg.startup_state import (
    PHASE_DEGRADED,
    PHASE_FIRST_RUN,
    PHASE_POST_SETUP_SYNC,
    PHASE_READY,
    PHASE_REPAIR,
    PHASE_SAFE_MODE,
    PHASE_SETUP_WAIT_2FA,
    PHASE_SETUP_WAIT_QR,
    startup_snapshot,
    sync_startup_state,
)
from deathtg.ui import CONSOLE_BANNER

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
RUNTIME_DIR = ROOT_DIR / "runtime"
INSTANCE_LOCK_PATH = RUNTIME_DIR / "dtg.lock"
PANEL_ACTIONS_PATH = RUNTIME_DIR / "panel_actions.jsonl"
RESTART_REQUEST_PATH = RUNTIME_DIR / "restart.request"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)

userbot_process = None
supervisor_stop = threading.Event()
restart_in_progress = threading.Event()
last_start_attempt = 0.0
MIN_RESTART_INTERVAL = 5.0


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _pid_is_running(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def acquire_instance_lock() -> bool:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if INSTANCE_LOCK_PATH.exists():
        try:
            data = json.loads(INSTANCE_LOCK_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        existing_pid = int(data.get("pid") or 0) if isinstance(data, dict) else 0
        if _pid_is_running(existing_pid):
            print(f"DeathTG is already running in another process (PID {existing_pid}).")
            print("Stop the old window/process first, then start DeathTG again.")
            return False
        try:
            INSTANCE_LOCK_PATH.unlink()
        except Exception:
            pass
    INSTANCE_LOCK_PATH.write_text(
        json.dumps({"pid": os.getpid(), "started_at": int(time.time())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


def release_instance_lock() -> None:
    try:
        data = json.loads(INSTANCE_LOCK_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict) or int(data.get("pid") or 0) == os.getpid():
        try:
            INSTANCE_LOCK_PATH.unlink()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeathTG full stack launcher")
    parser.add_argument("--repair", action="store_true", help="repair runtime layout/config before startup")
    parser.add_argument("--safe", action="store_true", help="start without userbot/modules; panel stays available")
    parser.add_argument("--no-panel", action="store_true", help="start userbot supervisor only")
    parser.add_argument("--no-modules", action="store_true", help="disable third-party modules for this run")
    parser.add_argument("--debug", action="store_true", help="use verbose uvicorn logging")
    parser.add_argument("--preflight-only", action="store_true", help="run startup checks and exit")
    return parser.parse_args()


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
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with PANEL_ACTIONS_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"action": "shutdown", "ts": int(time.time())}, ensure_ascii=False) + "\n")
        deadline = time.time() + max(1.0, timeout - 2.0)
        while time.time() < deadline and process.poll() is None:
            time.sleep(0.1)
        if process.poll() is not None:
            userbot_process = None
            return
    except Exception:
        pass
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


def _userbot_ready() -> bool:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
    return ready_to_start_userbot()


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
    time.sleep(0.8)
    if userbot_process.poll() is not None:
        code = userbot_process.returncode
        userbot_process = None
        print(f"Userbot: stopped during startup (exit {code})")
        return
    print("Userbot: started")


def supervisor_loop() -> None:
    while not supervisor_stop.is_set():
        try:
            ensure_userbot_running()
        except Exception as exc:
            print(f"Userbot supervisor warning: {type(exc).__name__}: {exc}")
        supervisor_stop.wait(2.0)


def restart_monitor_loop() -> None:
    while not supervisor_stop.is_set():
        if not RESTART_REQUEST_PATH.exists():
            supervisor_stop.wait(0.5)
            continue
        try:
            RESTART_REQUEST_PATH.unlink()
        except Exception:
            supervisor_stop.wait(0.5)
            continue
        if restart_in_progress.is_set():
            return
        restart_in_progress.set()
        supervisor_stop.set()
        stop_userbot()
        release_instance_lock()
        os.chdir(ROOT_DIR)
        os.execv(sys.executable, [sys.executable, str(ROOT_DIR / "dtg.py")])


def cleanup(signum=None, frame=None):
    supervisor_stop.set()
    stop_userbot()
    release_instance_lock()
    raise SystemExit(0)


def clear_console() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def panel_url() -> str:
    return panel_base_url()


def _port_is_available(host: str, port: int) -> bool:
    bind_host = host or "127.0.0.1"
    family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
        return True
    except OSError:
        return False


def _pick_panel_port(host: str, preferred: int) -> int:
    if _port_is_available(host, preferred):
        return preferred
    for candidate in range(preferred + 1, min(preferred + 100, 65535) + 1):
        if _port_is_available(host, candidate):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def normalize_panel_port() -> int:
    host = effective_panel_bind_host()
    raw_port = os.getenv("PANEL_PORT", "8080").strip() or "8080"
    try:
        preferred_port = max(1, min(65535, int(raw_port)))
    except ValueError:
        preferred_port = 8080
    chosen_port = _pick_panel_port(host, preferred_port)
    if chosen_port != preferred_port:
        update_env_values({"PANEL_PORT": str(chosen_port)}, path=ENV_PATH)
        os.environ["PANEL_PORT"] = str(chosen_port)
        print(f"Panel port {preferred_port} is busy, switching to {chosen_port}.")
    return chosen_port


def run_panel(debug: bool = False) -> None:
    host = effective_panel_bind_host()
    port = normalize_panel_port()
    uvicorn.run(
        "deathtg.panel.clean_app:app",
        host=host,
        port=port,
        log_level="info" if debug else "warning",
        access_log=debug,
    )


def main() -> int:
    configure_console_encoding()
    args = parse_args()
    if running_in_termux():
        print("DeathTG does not support Termux.")
        print("Use a normal Linux server, VPS, or desktop Python environment instead.")
        return 1
    if not acquire_instance_lock():
        return 1
    try:
        RESTART_REQUEST_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    atexit.register(release_instance_lock)

    ensure_server_env()
    report = run_preflight(
        repair=args.repair,
        safe=args.safe,
        no_panel=args.no_panel,
        no_modules=args.no_modules,
    )
    print_report(report)
    if args.preflight_only:
        release_instance_lock()
        return 0 if report.ok else 1
    if not report.ok:
        print("Startup stopped. Run: python dtg.py --repair")
        release_instance_lock()
        return 1

    if not args.no_panel:
        panel_port = normalize_panel_port()
        tailnet = ensure_tailscale_serve(panel_port)
    else:
        tailnet = {"message": "panel disabled"}

    sync_startup_state()
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    clear_console()
    print(CONSOLE_BANNER)
    print()
    snapshot = startup_snapshot()
    phase = str(snapshot.get("phase") or PHASE_FIRST_RUN)
    safe_runtime = args.safe or phase == PHASE_SAFE_MODE

    print("DeathTG full stack is starting...")
    print(f"Mode: {report.mode}")
    if not args.no_panel:
        print(f"Panel (this device): {panel_local_url()}")
        if panel_remote_access_ready():
            print(f"Panel (phone / PC): {panel_url()}")
        if tailnet.get("connected") and tailnet.get("url"):
            print(f"Panel (Tailscale): {tailnet['url']}")
        elif str(tailnet.get("message") or "").strip():
            print(f"Panel (Tailscale): {tailnet['message']}")
    if phase in {PHASE_FIRST_RUN, PHASE_SETUP_WAIT_QR, PHASE_SETUP_WAIT_2FA}:
        print(f"First run setup link: {setup_link()}")
    if phase == PHASE_FIRST_RUN:
        print("First run: open setup, enter API_ID/API_HASH, scan the QR code in Telegram, then enter 2FA only if Telegram asks for it.")
        print("Console never asks for the Telegram code. DeathTG waits for QR approval from the website flow and finishes login in the background.")
    elif phase == PHASE_SETUP_WAIT_QR:
        print("Setup is active: DeathTG is waiting for Telegram QR approval in the website flow.")
    elif phase == PHASE_SETUP_WAIT_2FA:
        print("Setup is active: Telegram requested the 2FA password in the website flow.")
    elif phase == PHASE_POST_SETUP_SYNC:
        print("Startup sync is running: DeathTG is recovering Telegram resources and finalizing setup.")
    elif phase == PHASE_SAFE_MODE:
        print("Safe mode is enabled: DeathTG will skip external local modules and only boot core runtime.")
    elif phase == PHASE_DEGRADED:
        print(f"Startup warning: {snapshot.get('message') or 'DeathTG started in degraded mode.'}")
    elif phase == PHASE_READY:
        print("Runtime state: ready.")
    elif phase == PHASE_REPAIR:
        print("Runtime state: repair flow is active.")
    if not os.getenv("PANEL_PUBLIC_URL", "").strip():
        print("Panel stays on localhost. Optional phone/server access is private through Tailscale Serve.")
    print("Userbot: will auto-start after setup and session creation." if not safe_runtime else "Safe mode: userbot/modules are disabled.")
    print("Git updates are not auto-applied. DeathTG will notify you in Telegram when a new update appears.")

    supervisor_thread = None
    restart_thread = threading.Thread(target=restart_monitor_loop, name="dtg-restart-monitor", daemon=True)
    restart_thread.start()
    if not safe_runtime:
        supervisor_thread = threading.Thread(target=supervisor_loop, name="dtg-userbot-supervisor", daemon=True)
        supervisor_thread.start()

    try:
        if args.no_panel:
            while True:
                time.sleep(1)
        else:
            run_panel(debug=args.debug)
    finally:
        supervisor_stop.set()
        if supervisor_thread:
            supervisor_thread.join(timeout=2.0)
        restart_thread.join(timeout=1.0)
        stop_userbot()
        release_instance_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
