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
import webbrowser
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
from deathtg.panel.lifecycle import SHUTDOWN_REQUESTED as PANEL_SHUTDOWN_REQUESTED
from deathtg.tailscale import (
    ensure_tailscale_access,
    install_tailscale_and_run_up,
    tailscale_listener_hosts,
    tailscale_status,
)
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
RESTART_PARENT_ENV = "DTG_RESTART_PARENT_PID"


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


def _wait_for_replaced_parent(timeout: float = 15.0) -> None:
    raw_pid = os.environ.pop(RESTART_PARENT_ENV, "").strip()
    try:
        parent_pid = int(raw_pid or "0")
    except ValueError:
        return
    if parent_pid <= 0 or parent_pid == os.getpid():
        return
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline and _pid_is_running(parent_pid):
        time.sleep(0.1)


def _console_yes(prompt: str, *, default: bool = False) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, OSError):
        return default
    if not answer:
        return default
    return answer in {"y", "yes", "д", "да"}


def _select_server_install(args: argparse.Namespace) -> bool:
    """Persist the local/server choice once, with flags for non-interactive installs."""
    if args.server_install is not None:
        value = bool(args.server_install)
        update_env_values(
            {"DEATHTG_SERVER_INSTALL_SELECTED": "1", "DEATHTG_SERVER_INSTALL": "1" if value else "0"},
            path=ENV_PATH,
        )
        return value

    if os.getenv("DEATHTG_SERVER_INSTALL_SELECTED", "").strip() == "1":
        return os.getenv("DEATHTG_SERVER_INSTALL", "").strip() == "1"

    # An explicit environment setting is useful when DeathTG is launched by systemd.
    configured = os.getenv("DEATHTG_SERVER_INSTALL", "").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        update_env_values({"DEATHTG_SERVER_INSTALL_SELECTED": "1"}, path=ENV_PATH)
        return True
    if configured in {"0", "false", "no", "off"}:
        update_env_values({"DEATHTG_SERVER_INSTALL_SELECTED": "1"}, path=ENV_PATH)
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False

    is_server = _console_yes("\nAre you running DeathTG on a remote server or VPS? [y/N]: ")
    update_env_values(
        {"DEATHTG_SERVER_INSTALL_SELECTED": "1", "DEATHTG_SERVER_INSTALL": "1" if is_server else "0"},
        path=ENV_PATH,
    )
    return is_server


def _tailnet_has_owner_device(status: dict) -> bool:
    owner_id = str(status.get("own_user_id") or "")
    if not owner_id:
        return False
    return any(
        isinstance(peer, dict)
        and peer.get("online")
        and str(peer.get("user_id") or "") == owner_id
        for peer in (status.get("peer_ips") or {}).values()
    )


def _prepare_remote_server_setup(panel_port: int) -> dict:
    """Connect this host and the user's device before exposing a setup URL."""
    update_env_values(
        {
            "PANEL_TAILSCALE_TRUST": "1",
            "PANEL_TAILSCALE_SERVE": "1",
            "PANEL_TAILSCALE_AUTO_SERVE": "1",
            "PANEL_TAILSCALE_DIRECT": "0",
            "PANEL_HOST": "127.0.0.1",
            "PANEL_ALLOW_REMOTE_BIND": "0",
            "PANEL_PUBLIC_URL": "",
            "PANEL_PUBLIC_HOST": "",
            "PANEL_SCHEME": "http",
            "PANEL_COOKIE_SECURE": "1",
        },
        path=ENV_PATH,
    )
    print("\nPRIVATE SERVER SETUP")
    print("1. Install Tailscale on your PC or phone and sign in to the account you will use for DeathTG.")
    print("   Download: https://tailscale.com/download")
    if not os.getenv("DEATHTG_TAILSCALE_CLIENT_READY", "").strip() == "1":
        if sys.stdin.isatty() and not _console_yes("Is Tailscale connected on your PC/phone? Press Y to continue. [y/N]: "):
            raise RuntimeError("Setup paused. Connect Tailscale on your PC/phone, then start DeathTG again.")
        update_env_values({"DEATHTG_TAILSCALE_CLIENT_READY": "1"}, path=ENV_PATH)

    print("2. DeathTG will connect this server to that same account. Approve its sign-in link below.")
    try:
        install_tailscale_and_run_up()
    except Exception as exc:
        raise RuntimeError(f"Could not start Tailscale on the server: {exc}") from exc

    if sys.stdin.isatty():
        while True:
            status = tailscale_status(refresh=True)
            if status.get("connected") and status.get("identity_bound") and _tailnet_has_owner_device(status):
                break
            print("Waiting for the server and your PC/phone to appear in the same Tailscale account.")
            if not _console_yes("After authorizing the server link above, press Y to check again. [y/N]: "):
                raise RuntimeError("Tailscale authorization is still required before opening server setup.")
    else:
        deadline = time.monotonic() + 15 * 60
        status = {}
        while time.monotonic() < deadline:
            status = tailscale_status(refresh=True)
            if status.get("connected") and status.get("identity_bound") and _tailnet_has_owner_device(status):
                break
            time.sleep(3)
        else:
            raise RuntimeError("Tailscale did not connect the server and your device to the same account within 15 minutes.")

    access = ensure_tailscale_access(panel_port)
    if not access.get("serve_ready") or not str(access.get("url") or "").startswith("https://"):
        detail = str(access.get("message") or "Tailscale Serve HTTPS is not ready")
        raise RuntimeError(f"The private HTTPS setup link was not created: {detail}")
    return access


def _spawn_replacement() -> bool:
    environment = os.environ.copy()
    environment[RESTART_PARENT_ENV] = str(os.getpid())
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT_DIR / "dtg.py")],
            cwd=ROOT_DIR,
            env=environment,
            close_fds=True,
        )
    except OSError as exc:
        print(f"DeathTG restart failed: {type(exc).__name__}: {exc}")
        return False
    return True


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
    install_target = parser.add_mutually_exclusive_group()
    install_target.add_argument("--server", dest="server_install", action="store_true", help="prepare private Tailscale HTTPS setup for a server")
    install_target.add_argument("--local", dest="server_install", action="store_false", help="run setup on this device at localhost")
    parser.set_defaults(server_install=None)
    parser.add_argument("--repair", action="store_true", help="repair runtime layout/config before startup")
    parser.add_argument("--choose-mode", action="store_true", help="temporarily reopen setup to switch between Web and Telegram-only")
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
        if not _spawn_replacement():
            restart_in_progress.clear()
            supervisor_stop.clear()
            continue
        release_instance_lock()
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        os._exit(0)


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


def _panel_listen_hosts() -> list[str]:
    primary = effective_panel_bind_host()
    hosts = [primary]
    if primary in {"127.0.0.1", "localhost", "::1"}:
        for host in tailscale_listener_hosts():
            if host not in hosts:
                hosts.append(host)
    return hosts


def _pick_panel_port(hosts: list[str], preferred: int) -> int:
    if all(_port_is_available(host, preferred) for host in hosts):
        return preferred
    for candidate in range(preferred + 1, min(preferred + 100, 65535) + 1):
        if all(_port_is_available(host, candidate) for host in hosts):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def normalize_panel_port() -> int:
    hosts = _panel_listen_hosts()
    raw_port = os.getenv("PANEL_PORT", "8080").strip() or "8080"
    try:
        preferred_port = max(1, min(65535, int(raw_port)))
    except ValueError:
        preferred_port = 8080
    chosen_port = _pick_panel_port(hosts, preferred_port)
    if chosen_port != preferred_port:
        update_env_values({"PANEL_PORT": str(chosen_port)}, path=ENV_PATH)
        os.environ["PANEL_PORT"] = str(chosen_port)
        print(f"Panel port {preferred_port} is busy, switching to {chosen_port}.")
    return chosen_port


def run_panel(debug: bool = False) -> None:
    port = normalize_panel_port()
    hosts = _panel_listen_hosts()
    config = uvicorn.Config(
        "deathtg.panel.clean_app:app",
        host=hosts[0],
        port=port,
        log_level="info" if debug else "warning",
        access_log=debug,
    )
    sockets: list[socket.socket] = []
    server = uvicorn.Server(config)
    PANEL_SHUTDOWN_REQUESTED.clear()

    def watch_panel_shutdown() -> None:
        while not PANEL_SHUTDOWN_REQUESTED.wait(0.2):
            if server.should_exit:
                return
        server.should_exit = True

    shutdown_watcher = threading.Thread(target=watch_panel_shutdown, name="dtg-panel-shutdown", daemon=True)
    try:
        for host in hosts:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            listener = socket.socket(family, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
            listener.set_inheritable(True)
            sockets.append(listener)
        shutdown_watcher.start()
        server.run(sockets=sockets)
    finally:
        PANEL_SHUTDOWN_REQUESTED.set()
        if shutdown_watcher.is_alive():
            shutdown_watcher.join(timeout=1.0)
        for listener in sockets:
            try:
                listener.close()
            except OSError:
                pass


def main() -> int:
    configure_console_encoding()
    args = parse_args()
    if running_in_termux():
        print("DeathTG does not support Termux.")
        print("Use a normal Linux server, VPS, or desktop Python environment instead.")
        return 1
    _wait_for_replaced_parent()
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
    if args.choose_mode:
        update_env_values(
            {"DEATHTG_MODE_SELECTED": "0"},
            path=ENV_PATH,
        )
        os.environ["DEATHTG_MODE_SELECTED"] = "0"
    clear_console()
    print(CONSOLE_BANNER)
    print()
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

    snapshot = startup_snapshot()
    phase = str(snapshot.get("phase") or PHASE_FIRST_RUN)
    mode_selected = os.getenv("DEATHTG_MODE_SELECTED", "").strip() == "1"
    selected_mode = os.getenv("DEATHTG_MODE", "").strip().lower()
    telegram_only_ready = mode_selected and selected_mode == "telegram" and not snapshot.get("setup_required")
    panel_enabled = not args.no_panel and not telegram_only_ready
    server_install = _select_server_install(args) if panel_enabled else os.getenv("DEATHTG_SERVER_INSTALL", "").strip() == "1"
    safe_runtime = args.safe or phase == PHASE_SAFE_MODE

    if panel_enabled:
        panel_port = normalize_panel_port()
        server_first_setup = server_install and not mode_selected
        server_setup_in_progress = server_install and selected_mode == "telegram" and snapshot.get("setup_required")
        if server_first_setup or server_setup_in_progress:
            try:
                tailnet = _prepare_remote_server_setup(panel_port)
            except Exception as exc:
                print(f"\nRemote setup is locked until Tailscale is ready: {exc}")
                print("DeathTG did not expose the setup website. Complete the Tailscale steps and run it again.")
                release_instance_lock()
                return 1
        elif mode_selected and selected_mode == "web":
            update_env_values(
                {
                    "PANEL_TAILSCALE_TRUST": "1",
                    "PANEL_TAILSCALE_SERVE": "1",
                    "PANEL_TAILSCALE_AUTO_SERVE": "1",
                    "PANEL_TAILSCALE_DIRECT": "0",
                    "PANEL_HOST": "127.0.0.1",
                    "PANEL_ALLOW_REMOTE_BIND": "0",
                    "PANEL_PUBLIC_URL": "",
                    "PANEL_PUBLIC_HOST": "",
                    "PANEL_SCHEME": "http",
                    "PANEL_COOKIE_SECURE": "1",
                },
                path=ENV_PATH,
            )
            status = tailscale_status(refresh=True)
            if not status.get("connected"):
                try:
                    install_tailscale_and_run_up()
                except Exception as exc:
                    print(f"Tailscale startup warning: {exc}")
            tailnet = ensure_tailscale_access(panel_port)
        else:
            update_env_values(
                {
                    "PANEL_TAILSCALE_TRUST": "0",
                    "PANEL_TAILSCALE_SERVE": "0",
                    "PANEL_TAILSCALE_AUTO_SERVE": "0",
                    "PANEL_TAILSCALE_DIRECT": "0",
                    "PANEL_HOST": "127.0.0.1",
                    "PANEL_ALLOW_REMOTE_BIND": "0",
                    "PANEL_PUBLIC_URL": "",
                    "PANEL_PUBLIC_HOST": "",
                    "PANEL_SCHEME": "http",
                    "PANEL_COOKIE_SECURE": "0",
                },
                path=ENV_PATH,
            )
            tailnet = {"message": "Tailscale is off until you choose Web dashboard"}
    else:
        tailnet = {"message": "Panel disabled by Telegram-only mode" if telegram_only_ready else "panel disabled"}

    sync_startup_state()
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print("DeathTG full stack is starting...")
    print(f"Mode: {report.mode}")
    if panel_enabled:
        if server_install and tailnet.get("serve_ready"):
            print(f"Private panel (Tailscale HTTPS): {tailnet['url']}")
        else:
            print(f"Panel (this device): {panel_local_url()}")
        if panel_remote_access_ready() and not server_install:
            print(f"Panel (phone / PC): {panel_url()}")
        if tailnet.get("connected") and tailnet.get("url"):
            print(f"Panel (Tailscale HTTPS): {tailnet['url']}")
        elif str(tailnet.get("message") or "").strip():
            print(f"Tailscale: {tailnet['message']}")
    if phase in {PHASE_FIRST_RUN, PHASE_SETUP_WAIT_QR, PHASE_SETUP_WAIT_2FA} or not mode_selected:
        first_run_url = setup_link()
        link_kind = "private HTTPS setup link (one-time token)" if server_install else "local setup link (one-time token)"
        print(f"Setup link ({link_kind}): {first_run_url}")
        if os.name == "nt" and sys.stdout.isatty() and not server_install:
            webbrowser.open(first_run_url, new=2)
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
        if os.name == "nt" and sys.stdout.isatty() and panel_enabled and not server_install:
            webbrowser.open(panel_local_url(), new=2)
    elif phase == PHASE_REPAIR:
        print("Runtime state: repair flow is active.")
    if telegram_only_ready:
        print("Telegram-only mode: the setup website is off. Use Telegram to control DeathTG.")
    elif not server_install and not (tailnet.get("serve_ready") or os.getenv("PANEL_PUBLIC_URL", "").strip()):
        print("The panel is local-only until Web dashboard is selected.")
    print("Userbot: will auto-start after setup and session creation." if not safe_runtime else "Safe mode: userbot/modules are disabled.")
    print("Git updates are not auto-applied. DeathTG will notify you in Telegram when a new update appears.")

    supervisor_thread = None
    restart_thread = threading.Thread(target=restart_monitor_loop, name="dtg-restart-monitor", daemon=True)
    restart_thread.start()
    if not safe_runtime:
        supervisor_thread = threading.Thread(target=supervisor_loop, name="dtg-userbot-supervisor", daemon=True)
        supervisor_thread.start()

    try:
        if not panel_enabled:
            while True:
                time.sleep(1)
        else:
            run_panel(debug=args.debug)
            final_snapshot = startup_snapshot()
            if (
                os.getenv("DEATHTG_MODE_SELECTED", "").strip() == "1"
                and os.getenv("DEATHTG_MODE", "").strip().lower() == "telegram"
                and not final_snapshot.get("setup_required")
            ):
                print("Telegram-only setup is complete. DeathTG website is now closed; userbot stays active.")
                while True:
                    time.sleep(1)
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
