from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from deathtg.config import RUNTIME_DIR


_CACHE_TTL = 12.0
_CACHE_LOCK = threading.Lock()
_CACHE: tuple[float, dict[str, Any]] = (0.0, {})
_BINDING_LOCK = threading.Lock()
TAILSCALE_BINDING_PATH = RUNTIME_DIR / "tailscale_binding.json"


def _enabled() -> bool:
    value = os.getenv("PANEL_TAILSCALE_TRUST", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _serve_enabled() -> bool:
    value = os.getenv("PANEL_TAILSCALE_SERVE", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _direct_enabled() -> bool:
    value = os.getenv("PANEL_TAILSCALE_DIRECT", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _auto_serve_enabled() -> bool:
    value = os.getenv("PANEL_TAILSCALE_AUTO_SERVE", "0").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _normalize_ip(value: str) -> str:
    raw = (value or "").strip().split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return ""


def _tailscale_command() -> str:
    found = shutil.which("tailscale") or shutil.which("tailscale.exe")
    if found:
        return found
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Tailscale" / "tailscale.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
    return ""


def _empty_status(message: str = "Tailscale is not installed") -> dict[str, Any]:
    return {
        "available": False,
        "connected": False,
        "trusted_access": _enabled(),
        "backend_state": "Unavailable",
        "hostname": "",
        "dns_name": "",
        "ips": [],
        "peer_ips": {},
        "url": "",
        "access_mode": "local",
        "serve_ready": False,
        "serve_url": "",
        "direct_ready": False,
        "direct_url": "",
        "identity_bound": False,
        "identity_message": message,
        "own_user_id": "",
        "login_name": "",
        "tailnet_name": "",
        "magic_dns_suffix": "",
        "node_id": "",
        "message": message,
    }


def _run_status(command: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 4,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run([command, "status", "--json"], **kwargs)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Tailscale is not running").strip()
        raise RuntimeError(detail[:240])
    payload = json.loads(completed.stdout or "{}")
    return payload if isinstance(payload, dict) else {}


def _build_status(payload: dict[str, Any], command: str) -> dict[str, Any]:
    own = payload.get("Self") if isinstance(payload.get("Self"), dict) else {}
    backend_state = str(payload.get("BackendState") or "Unknown")
    own_ips = [ip for value in own.get("TailscaleIPs", []) if (ip := _normalize_ip(str(value)))]
    dns_name = str(own.get("DNSName") or "").rstrip(".")
    hostname = str(own.get("HostName") or payload.get("HostName") or "")
    own_user_id = str(own.get("UserID") or "")
    current_tailnet = payload.get("CurrentTailnet") if isinstance(payload.get("CurrentTailnet"), dict) else {}
    tailnet_name = str(current_tailnet.get("Name") or "")
    magic_dns_suffix = str(
        current_tailnet.get("MagicDNSSuffix") or payload.get("MagicDNSSuffix") or ""
    ).rstrip(".")
    users = payload.get("User") if isinstance(payload.get("User"), dict) else {}
    own_user = users.get(own_user_id) if isinstance(users.get(own_user_id), dict) else {}
    login_name = str(own_user.get("LoginName") or tailnet_name or "")
    peer_ips: dict[str, dict[str, Any]] = {}
    peers = payload.get("Peer") if isinstance(payload.get("Peer"), dict) else {}
    for peer in peers.values():
        if not isinstance(peer, dict):
            continue
        identity = {
            "hostname": str(peer.get("HostName") or "Tailscale device"),
            "dns_name": str(peer.get("DNSName") or "").rstrip("."),
            "user_id": str(peer.get("UserID") or ""),
            "online": bool(peer.get("Online", False)),
        }
        for value in peer.get("TailscaleIPs", []):
            normalized = _normalize_ip(str(value))
            if normalized:
                peer_ips[normalized] = identity

    connected = backend_state.lower() == "running" and bool(own_ips)
    serve_url = f"https://{dns_name}" if connected and dns_name else ""
    return {
        "available": True,
        "connected": connected,
        "trusted_access": _enabled(),
        "backend_state": backend_state,
        "hostname": hostname,
        "dns_name": dns_name,
        "ips": own_ips,
        "peer_ips": peer_ips,
        "url": "",
        "access_mode": "local",
        "serve_ready": False,
        "serve_url": serve_url,
        "direct_ready": False,
        "direct_url": "",
        "identity_bound": False,
        "identity_message": "Tailscale identity has not been checked",
        "own_user_id": own_user_id,
        "login_name": login_name,
        "tailnet_name": tailnet_name,
        "magic_dns_suffix": magic_dns_suffix,
        "node_id": str(own.get("ID") or ""),
        "command": command,
        "message": "Tailnet access is ready" if connected else f"Tailscale state: {backend_state}",
    }


def _read_binding(path: Path | None = None) -> dict[str, str]:
    path = path or TAILSCALE_BINDING_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value or "") for key, value in payload.items()}


def _write_binding(binding: dict[str, str], path: Path | None = None) -> None:
    path = path or TAILSCALE_BINDING_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _identity_values(status: dict[str, Any]) -> dict[str, str]:
    return {
        "version": "1",
        "user_id": str(status.get("own_user_id") or ""),
        "login_name": str(status.get("login_name") or "").strip().lower(),
        "tailnet_name": str(status.get("tailnet_name") or "").strip().lower(),
        "magic_dns_suffix": str(status.get("magic_dns_suffix") or "").strip().lower(),
        "hostname": str(status.get("hostname") or "").strip().lower(),
        "node_id": str(status.get("node_id") or ""),
    }


def _apply_identity_binding(status: dict[str, Any]) -> dict[str, Any]:
    result = dict(status)
    if not result.get("connected"):
        result["identity_bound"] = False
        result["identity_message"] = str(result.get("message") or "Tailscale is offline")
        return result

    current = _identity_values(result)
    required = ("user_id", "login_name", "magic_dns_suffix", "hostname")
    if any(not current.get(key) for key in required):
        result["identity_bound"] = False
        result["identity_message"] = "Tailscale did not return a complete local identity"
        return result

    try:
        with _BINDING_LOCK:
            binding = _read_binding()
            if not binding:
                _write_binding(current)
                binding = dict(current)
    except OSError as exc:
        result["identity_bound"] = False
        result["identity_message"] = f"Tailscale identity binding failed: {type(exc).__name__}"
        return result

    mismatches = [
        key
        for key in required
        if str(binding.get(key) or "").strip().lower() != str(current.get(key) or "").strip().lower()
    ]
    expected_login = os.getenv("PANEL_TAILSCALE_EXPECTED_LOGIN", "").strip().lower()
    if expected_login and current["login_name"] != expected_login:
        mismatches.append("expected_login")

    result["identity_bound"] = not mismatches
    result["identity_message"] = (
        "Tailscale identity is bound to this DeathTG installation"
        if not mismatches
        else "Tailscale identity changed; remote panel access is disabled"
    )
    return result


def _run_serve_status(command: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 5,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run([command, "serve", "status", "--json"], **kwargs)
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _serve_targets_port(payload: dict[str, Any], port: int) -> bool:
    serialized = json.dumps(payload, sort_keys=True).lower()
    return any(
        target in serialized
        for target in (
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        )
    )


def _direct_access_url(status: dict[str, Any], port: int) -> str:
    host = str(status.get("dns_name") or "").strip().rstrip(".")
    if not host:
        ipv4 = next(
            (value for value in status.get("ips", []) if ":" not in str(value)),
            "",
        )
        host = str(ipv4)
    if not host:
        return ""
    return f"http://{host}:{int(port)}"


def _cache_access_state(
    *,
    mode: str,
    url: str = "",
    serve_ready: bool = False,
    direct_ready: bool = False,
    message: str = "",
) -> dict[str, Any]:
    global _CACHE
    with _CACHE_LOCK:
        cached_at, cached = _CACHE
        result = dict(cached)
        result["access_mode"] = str(mode or "local")
        result["serve_ready"] = bool(serve_ready)
        result["direct_ready"] = bool(direct_ready)
        result["direct_url"] = str(url) if direct_ready else ""
        result["url"] = str(url or "")
        if result["url"]:
            os.environ["PANEL_TAILSCALE_URL"] = str(result["url"])
        else:
            os.environ.pop("PANEL_TAILSCALE_URL", None)
        if message:
            result["message"] = message
        _CACHE = (cached_at or time.monotonic(), dict(result))
    return result


def ensure_tailscale_access(port: int) -> dict[str, Any]:
    """Prepare private tailnet access without ever opening a LAN listener."""
    status = tailscale_status(refresh=True)
    if not status.get("connected"):
        return _cache_access_state(
            mode="local",
            message=str(status.get("message") or "Tailscale is offline"),
        )
    if not status.get("identity_bound"):
        return _cache_access_state(
            mode="local",
            message=str(status.get("identity_message") or "Tailscale identity mismatch"),
        )
    command = str(status.get("command") or "")
    if _serve_enabled() and command:
        try:
            serve_status = _run_serve_status(command)
        except (OSError, subprocess.SubprocessError):
            serve_status = {}
        if _serve_targets_port(serve_status, int(port)):
            serve_url = str(status.get("serve_url") or "")
            return _cache_access_state(
                mode="serve",
                url=serve_url,
                serve_ready=True,
                message="Private Tailnet HTTPS is ready",
            )

        if _auto_serve_enabled():
            kwargs: dict[str, Any] = {
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 6,
                "check": False,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            target = f"http://127.0.0.1:{int(port)}"
            try:
                completed = subprocess.run([command, "serve", "--bg", "--yes", target], **kwargs)
            except (OSError, subprocess.SubprocessError):
                completed = None
            if completed is not None and completed.returncode == 0:
                serve_url = str(status.get("serve_url") or "")
                return _cache_access_state(
                    mode="serve",
                    url=serve_url,
                    serve_ready=True,
                    message="Private Tailnet HTTPS is ready",
                )

    if _direct_enabled():
        direct_url = _direct_access_url(status, int(port))
        if direct_url and tailscale_listener_hosts(status=status):
            return _cache_access_state(
                mode="direct",
                url=direct_url,
                direct_ready=True,
                message="Private Tailnet listener is ready",
            )

    return _cache_access_state(
        mode="local",
        message="Tailscale remote access is unavailable; panel remains localhost-only",
    )


def ensure_tailscale_serve(port: int) -> dict[str, Any]:
    """Backward-compatible alias for the private tailnet access bootstrap."""
    return ensure_tailscale_access(port)


def tailscale_status(*, refresh: bool = False) -> dict[str, Any]:
    global _CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        cached_at, cached = _CACHE
        if not refresh and cached and now - cached_at < _CACHE_TTL:
            return dict(cached)

    command = _tailscale_command()
    if not command:
        result = _empty_status()
    else:
        try:
            result = _apply_identity_binding(_build_status(_run_status(command), command))
            if result.get("connected") and _serve_enabled():
                try:
                    port = int(os.getenv("PANEL_PORT", "8080").strip() or "8080")
                except ValueError:
                    port = 8080
                if _serve_targets_port(_run_serve_status(command), port):
                    result["serve_ready"] = True
                    result["url"] = result.get("serve_url", "")
                    if result["url"]:
                        os.environ["PANEL_TAILSCALE_URL"] = str(result["url"])
        except Exception as exc:
            result = _empty_status(f"{type(exc).__name__}: {exc}")
            result["available"] = True
            result["command"] = command

    with _CACHE_LOCK:
        _CACHE = (now, dict(result))
    return result


def tailscale_peer(client_ip: str) -> dict[str, Any] | None:
    if not _enabled():
        return None
    normalized = _normalize_ip(client_ip)
    if not normalized:
        return None
    status = tailscale_status()
    if not status.get("connected") or not status.get("identity_bound"):
        return None
    if normalized in status.get("ips", []):
        return {
            "hostname": status.get("hostname") or "This Tailscale device",
            "dns_name": status.get("dns_name") or "",
            "user_id": "self",
            "online": True,
            "ip": normalized,
        }
    peer = status.get("peer_ips", {}).get(normalized)
    if not isinstance(peer, dict):
        return None
    own_user_id = str(status.get("own_user_id") or "")
    if not own_user_id or str(peer.get("user_id") or "") != own_user_id:
        return None
    return dict(peer, ip=normalized)


def tailscale_listener_hosts(*, status: dict[str, Any] | None = None) -> list[str]:
    if not _enabled() or not _direct_enabled():
        return []
    current = dict(status or tailscale_status())
    if not current.get("connected") or not current.get("identity_bound"):
        return []
    if current.get("serve_ready"):
        return []
    hosts: list[str] = []
    for value in current.get("ips", []):
        normalized = _normalize_ip(str(value))
        if normalized and ":" not in normalized and normalized not in hosts:
            hosts.append(normalized)
    return hosts


def tailscale_allowed_hosts() -> list[str]:
    status = tailscale_status()
    hosts = [str(item) for item in status.get("ips", []) if item]
    for value in (status.get("dns_name"), status.get("hostname")):
        if value:
            hosts.append(str(value).rstrip("."))
    return hosts
