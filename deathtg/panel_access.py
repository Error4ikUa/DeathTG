from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import time
import base64
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from urllib.parse import urlparse

from deathtg.config import RUNTIME_DIR

GRANTS_PATH = RUNTIME_DIR / "panel_device_grants.json"
DEVICES_PATH = RUNTIME_DIR / "panel_devices.json"
WSL_PUBLISH_STATE_PATH = RUNTIME_DIR / "wsl_publish_state.json"
DEFAULT_GRANT_TTL = 60 * 60 * 24 * 7


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def running_in_wsl() -> bool:
    if _env("WSL_DISTRO_NAME") or _env("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except Exception:
        return False


def local_network_ip() -> str:
    override = _env("PANEL_LOCAL_IP")
    if override:
        return override
    if running_in_wsl():
        host_ip = _wsl_windows_host_ip()
        if host_ip:
            return host_ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
        if _usable_ipv4(host):
            return host
    except Exception:
        pass
    return ""


def _usable_ipv4(value: str) -> bool:
    raw = (value or "").strip()
    try:
        ip = ipaddress.ip_address(raw)
    except Exception:
        return False
    if ip.version != 4:
        return False
    if ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast:
        return False
    last_octet = raw.rsplit(".", 1)[-1]
    if last_octet in {"0", "255"}:
        return False
    return True


def _wsl_windows_host_ip() -> str:
    commands = [
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "$ip = Get-NetIPAddress -AddressFamily IPv4 | "
            "Where-Object { $_.IPAddress -notmatch '^(127\\.|169\\.254\\.)' } | "
            "Sort-Object -Property InterfaceMetric,SkipAsSource | "
            "Select-Object -First 1 -ExpandProperty IPAddress; "
            "if ($ip) { $ip }",
        ],
        ["cmd.exe", "/c", "ipconfig"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        except Exception:
            continue
        output = f"{completed.stdout}\n{completed.stderr}"
        for candidate in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", output):
            if _usable_ipv4(candidate):
                return candidate
    try:
        resolv = Path("/etc/resolv.conf")
        if resolv.exists():
            for line in resolv.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("nameserver "):
                    candidate = line.split(None, 1)[1].strip()
                    if _usable_ipv4(candidate):
                        return candidate
    except Exception:
        pass
    return ""


def _wsl_guest_ip() -> str:
    commands = [
        ["hostname", "-I"],
        ["sh", "-lc", "ip -4 -o addr show scope global | awk '{print $4}'"],
    ]
    for command in commands:
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=6, check=False)
        except Exception:
            continue
        output = f"{completed.stdout}\n{completed.stderr}"
        for candidate in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", output):
            if _usable_ipv4(candidate):
                return candidate
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            candidate = sock.getsockname()[0]
        if _usable_ipv4(candidate):
            return candidate
    except Exception:
        pass
    return ""


def _wsl_portproxy_ready(port: int | None = None) -> bool:
    listen_port = int(port or int(_env("PANEL_PORT", "8080") or "8080"))
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "netsh interface portproxy show v4tov4"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return False
    output = f"{completed.stdout}\n{completed.stderr}"
    return f":{listen_port}" in output or f" {listen_port} " in output


def _panel_port() -> int:
    raw = _env("PANEL_PORT", "8080") or "8080"
    try:
        return max(1, min(65535, int(raw)))
    except Exception:
        return 8080


def _wsl_publish_state() -> dict:
    if not WSL_PUBLISH_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(WSL_PUBLISH_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_wsl_publish_state(payload: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    WSL_PUBLISH_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _powershell_run(script: str, *, timeout: int = 20) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return None


def _encode_powershell(script: str) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _wsl_rule_name(port: int) -> str:
    return f"DeathTG {port}"


def _wsl_publish_script(port: int) -> str:
    rule_name = _wsl_rule_name(port).replace("'", "''")
    guest_ip = _wsl_guest_ip() or "127.0.0.1"
    return f"""
$ErrorActionPreference = 'Stop'
& netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport={port} | Out-Null
& netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport={port} connectaddress={guest_ip} connectport={port} | Out-Null
& netsh advfirewall firewall delete rule name='{rule_name}' protocol=TCP localport={port} | Out-Null
& netsh advfirewall firewall add rule name='{rule_name}' dir=in action=allow protocol=TCP localport={port} | Out-Null
Write-Output 'OK'
""".strip()


def ensure_wsl_public_access(*, request_elevation: bool = True) -> dict[str, str | bool]:
    if not running_in_wsl():
        return {"applicable": False, "ready": True, "message": "Not running in WSL."}
    if _env("PANEL_PUBLIC_URL") or _env("PANEL_PUBLIC_HOST"):
        return {"applicable": True, "ready": True, "message": "Public panel URL already configured."}

    port = _panel_port()
    if _wsl_portproxy_ready(port):
        state = {
            "last_status": "ready",
            "last_port": port,
            "last_checked_at": int(time.time()),
            "last_message": "Windows port forwarding is active.",
        }
        _write_wsl_publish_state(state)
        return {"applicable": True, "ready": True, "message": "Windows port forwarding is active."}

    script = _wsl_publish_script(port)
    direct = _powershell_run(script, timeout=25)
    if direct and direct.returncode == 0 and _wsl_portproxy_ready(port):
        state = {
            "last_status": "ready",
            "last_port": port,
            "last_checked_at": int(time.time()),
            "last_message": "Windows port forwarding was configured automatically.",
        }
        _write_wsl_publish_state(state)
        return {"applicable": True, "ready": True, "message": "Windows port forwarding was configured automatically."}

    error_output = ""
    if direct is not None:
        error_output = (f"{direct.stdout}\n{direct.stderr}").strip()

    if request_elevation:
        state = _wsl_publish_state()
        now = int(time.time())
        last_prompt = int(state.get("last_elevation_prompt_at", 0) or 0)
        if not last_prompt or now - last_prompt >= 300:
            encoded = _encode_powershell(script)
            elevate = _powershell_run(
                (
                    "Start-Process powershell "
                    "-Verb RunAs "
                    "-ArgumentList "
                    f"' -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}'"
                ),
                timeout=10,
            )
            state.update(
                {
                    "last_status": "elevation_requested",
                    "last_port": port,
                    "last_checked_at": now,
                    "last_elevation_prompt_at": now,
                    "last_message": "Requested Windows administrator access to publish the panel for phone and PC access.",
                    "last_error": error_output,
                }
            )
            _write_wsl_publish_state(state)
            if elevate and elevate.returncode == 0:
                return {
                    "applicable": True,
                    "ready": False,
                    "message": "Requested Windows administrator access to finish publishing the panel.",
                }

    state = {
        "last_status": "failed",
        "last_port": port,
        "last_checked_at": int(time.time()),
        "last_message": "Windows port forwarding is not active yet.",
        "last_error": error_output,
    }
    _write_wsl_publish_state(state)
    return {
        "applicable": True,
        "ready": False,
        "message": "Windows port forwarding is not active yet.",
    }


def _probe_url_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(getattr(response, "status", 0) or 0) == 200
    except Exception:
        return False


def _wsl_remote_probe_ok() -> bool:
    host = _wsl_windows_host_ip()
    if not host:
        return False
    port = _panel_port()
    return _probe_url_ok(f"http://{host}:{port}/healthz", timeout=2.0)


def effective_panel_bind_host() -> str:
    configured = _env("PANEL_HOST")
    if configured:
        return configured
    if _env("PANEL_PUBLIC_URL") or _env("PANEL_PUBLIC_HOST"):
        return "0.0.0.0"
    return "0.0.0.0"


def visible_panel_host() -> str:
    host = _env("PANEL_PUBLIC_HOST") or effective_panel_bind_host()
    if running_in_wsl() and not (_env("PANEL_PUBLIC_URL") or _env("PANEL_PUBLIC_HOST")):
        if not (_wsl_portproxy_ready() and _wsl_remote_probe_ok()):
            return "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        lan_ip = local_network_ip()
        return lan_ip or "127.0.0.1"
    return host or "127.0.0.1"


def _normalize_visible_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    if host not in {"0.0.0.0", "::", "localhost"}:
        return raw.rstrip("/")
    scheme = parsed.scheme or _env("PANEL_SCHEME", "http") or "http"
    visible_host = visible_panel_host()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        return f"{scheme}://{visible_host}:{port}"
    return f"{scheme}://{visible_host}"


def panel_base_url() -> str:
    full = _env("PANEL_PUBLIC_URL")
    if full:
        return _normalize_visible_url(full)
    scheme = _env("PANEL_SCHEME", "http") or "http"
    host = visible_panel_host()
    port = _env("PANEL_PORT", "8080") or "8080"
    if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def panel_local_url() -> str:
    scheme = _env("PANEL_SCHEME", "http") or "http"
    port = _env("PANEL_PORT", "8080") or "8080"
    if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
        return f"{scheme}://127.0.0.1"
    return f"{scheme}://127.0.0.1:{port}"


def panel_site_id() -> str:
    raw = _env("PANEL_SITE_ID")
    slug = re.sub(r"[^a-zA-Z0-9_-]", "", raw)[:48]
    return slug or "dtg"


def panel_private_route(owner_id: int | None = None, token: str = "") -> str:
    owner_value = owner_id
    if owner_value is None:
        try:
            owner_value = int(_env("OWNER_ID") or "0")
        except Exception:
            owner_value = 0
    route = f"/site/{panel_site_id()}/u{max(0, int(owner_value or 0))}"
    if token:
        return f"{route}/{token}"
    return route


def panel_private_url(owner_id: int | None = None, token: str = "") -> str:
    return f"{panel_base_url()}{panel_private_route(owner_id, token)}"


def panel_host_kind() -> str:
    base = panel_base_url()
    parsed = urlparse(base)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "unknown"
    if host in {"127.0.0.1", "localhost", "::1"}:
        return "loopback"
    return "remote"


def panel_remote_access_ready() -> bool:
    if running_in_wsl() and not (_env("PANEL_PUBLIC_URL") or _env("PANEL_PUBLIC_HOST")):
        if not _wsl_portproxy_ready():
            return False
        if not _wsl_remote_probe_ok():
            return False
    return panel_host_kind() == "remote"


def public_panel_enabled() -> bool:
    return bool((_env("PANEL_PUBLIC_URL") or _env("PANEL_PUBLIC_HOST")) and panel_base_url().startswith("https://"))


def _serializer() -> URLSafeTimedSerializer:
    secret = _env("PANEL_SECRET")
    if not secret:
        secret = secrets.token_urlsafe(32)
    return URLSafeTimedSerializer(secret_key=secret, salt="deathtg-panel-device")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup_grants(data: dict) -> dict:
    now = int(time.time())
    cleaned: dict[str, dict] = {}
    for grant_id, item in data.items():
        if not isinstance(item, dict):
            continue
        if item.get("revoked"):
            continue
        expires_at = int(item.get("expires_at", 0) or 0)
        if expires_at and expires_at < now:
            continue
        cleaned[str(grant_id)] = item
    return cleaned


def _cleanup_devices(data: dict) -> dict:
    cleaned: dict[str, dict] = {}
    for session_id, item in data.items():
        if not isinstance(item, dict):
            continue
        if item.get("revoked"):
            continue
        cleaned[str(session_id)] = item
    return cleaned


def friendly_device_name(user_agent: str = "", fallback: str = "Browser") -> str:
    ua = (user_agent or "").lower()
    if "iphone" in ua:
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    if "android" in ua:
        return "Android"
    if "windows" in ua:
        return "Windows PC"
    if "mac os" in ua or "macintosh" in ua:
        return "Mac"
    if "linux" in ua:
        return "Linux"
    return fallback


def list_devices() -> list[dict]:
    data = _cleanup_devices(_read_json(DEVICES_PATH))
    _write_json(DEVICES_PATH, data)
    return sorted(
        [dict(item, session_id=session_id) for session_id, item in data.items()],
        key=lambda item: int(item.get("last_seen_at", 0) or item.get("created_at", 0) or 0),
        reverse=True,
    )


def issue_device_grant(
    device_name: str,
    *,
    ttl_seconds: int = DEFAULT_GRANT_TTL,
    created_by: str = "panel",
    owner_id: int | None = None,
) -> str:
    grant_id = secrets.token_urlsafe(12)
    grant_secret = secrets.token_urlsafe(24)
    now = int(time.time())
    grants = _cleanup_grants(_read_json(GRANTS_PATH))
    grants[grant_id] = {
        "device_name": (device_name or "New device").strip()[:80],
        "grant_id": grant_id,
        "secret_hash": _hash(grant_secret),
        "created_at": now,
        "expires_at": now + max(300, int(ttl_seconds)),
        "created_by": created_by,
        "used": False,
        "used_at": 0,
        "revoked": False,
    }
    _write_json(GRANTS_PATH, grants)
    token = _serializer().dumps({"gid": grant_id, "sec": grant_secret})
    return panel_private_url(owner_id=owner_id, token=token)


def consume_device_grant(token: str, *, ip: str = "", user_agent: str = "") -> dict:
    try:
        payload = _serializer().loads(token)
    except SignatureExpired as exc:
        raise RuntimeError("Grant link expired") from exc
    except BadSignature as exc:
        raise RuntimeError("Grant link is invalid") from exc

    grant_id = str(payload.get("gid") or "")
    grant_secret = str(payload.get("sec") or "")
    if not grant_id or not grant_secret:
        raise RuntimeError("Grant link payload is incomplete")

    now = int(time.time())
    grants = _cleanup_grants(_read_json(GRANTS_PATH))
    grant = grants.get(grant_id)
    if not isinstance(grant, dict):
        raise RuntimeError("Grant link not found")
    if grant.get("revoked"):
        raise RuntimeError("Grant link revoked")
    if grant.get("used"):
        raise RuntimeError("Grant link already used")
    if _hash(grant_secret) != str(grant.get("secret_hash") or ""):
        raise RuntimeError("Grant link verification failed")
    expires_at = int(grant.get("expires_at", 0) or 0)
    if expires_at and expires_at < now:
        raise RuntimeError("Grant link expired")

    session_id = secrets.token_urlsafe(18)
    devices = _cleanup_devices(_read_json(DEVICES_PATH))
    devices[session_id] = {
        "label": grant.get("device_name") or friendly_device_name(user_agent, "Browser"),
        "created_at": now,
        "last_seen_at": now,
        "last_ip": ip,
        "user_agent": user_agent[:240],
        "auth_method": "grant",
        "grant_id": grant_id,
        "revoked": False,
    }
    grant["used"] = True
    grant["used_at"] = now
    grants[grant_id] = grant
    _write_json(DEVICES_PATH, devices)
    _write_json(GRANTS_PATH, grants)
    return {"session_id": session_id, "device": dict(devices[session_id])}


def remember_device_session(session_id: str, *, ip: str = "", user_agent: str = "", label: str = "", auth_method: str = "password") -> dict:
    now = int(time.time())
    devices = _cleanup_devices(_read_json(DEVICES_PATH))
    item = devices.get(session_id, {})
    if not isinstance(item, dict):
        item = {}
    item.update(
        {
            "label": label.strip()[:80] or item.get("label") or friendly_device_name(user_agent, "Browser"),
            "created_at": int(item.get("created_at", now) or now),
            "last_seen_at": now,
            "last_ip": ip,
            "user_agent": user_agent[:240],
            "auth_method": auth_method,
            "revoked": False,
        }
    )
    devices[session_id] = item
    _write_json(DEVICES_PATH, devices)
    return dict(item, session_id=session_id)


def touch_device_session(session_id: str, *, ip: str = "", user_agent: str = "") -> dict | None:
    if not session_id:
        return None
    devices = _cleanup_devices(_read_json(DEVICES_PATH))
    item = devices.get(session_id)
    if not isinstance(item, dict):
        return None
    item["last_seen_at"] = int(time.time())
    if ip:
        item["last_ip"] = ip
    if user_agent:
        item["user_agent"] = user_agent[:240]
    devices[session_id] = item
    _write_json(DEVICES_PATH, devices)
    return dict(item, session_id=session_id)


def revoke_device_session(session_id: str) -> None:
    devices = _read_json(DEVICES_PATH)
    item = devices.get(session_id)
    if isinstance(item, dict):
        item["revoked"] = True
        item["revoked_at"] = int(time.time())
        devices[session_id] = item
        _write_json(DEVICES_PATH, devices)


def active_device(session_id: str) -> dict | None:
    devices = _cleanup_devices(_read_json(DEVICES_PATH))
    item = devices.get(session_id)
    if not isinstance(item, dict):
        return None
    return dict(item, session_id=session_id)
