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


_CACHE_TTL = 12.0
_CACHE_LOCK = threading.Lock()
_CACHE: tuple[float, dict[str, Any]] = (0.0, {})


def _enabled() -> bool:
    value = os.getenv("PANEL_TAILSCALE_TRUST", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _serve_enabled() -> bool:
    value = os.getenv("PANEL_TAILSCALE_SERVE", "1").strip().lower()
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
        "serve_ready": False,
        "serve_url": "",
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
        "serve_ready": False,
        "serve_url": serve_url,
        "command": command,
        "message": "Tailnet access is ready" if connected else f"Tailscale state: {backend_state}",
    }


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


def _cache_serve_state(*, ready: bool, message: str = "") -> dict[str, Any]:
    global _CACHE
    with _CACHE_LOCK:
        cached_at, cached = _CACHE
        result = dict(cached)
        result["serve_ready"] = bool(ready)
        result["url"] = result.get("serve_url", "") if ready else ""
        if result["url"]:
            os.environ["PANEL_TAILSCALE_URL"] = str(result["url"])
        else:
            os.environ.pop("PANEL_TAILSCALE_URL", None)
        if message:
            result["message"] = message
        _CACHE = (cached_at or time.monotonic(), dict(result))
    return result


def ensure_tailscale_serve(port: int) -> dict[str, Any]:
    """Expose the loopback panel only inside the active tailnet."""
    status = tailscale_status(refresh=True)
    if not _serve_enabled():
        return _cache_serve_state(ready=False, message="Tailscale Serve is disabled")
    if not status.get("connected"):
        return _cache_serve_state(ready=False, message=str(status.get("message") or "Tailscale is offline"))
    command = str(status.get("command") or "")
    if not command:
        return _cache_serve_state(ready=False, message="Tailscale CLI is unavailable")
    if _serve_targets_port(_run_serve_status(command), int(port)):
        return _cache_serve_state(ready=True, message="Private Tailnet HTTPS is ready")

    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 15,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    target = f"http://127.0.0.1:{int(port)}"
    completed = subprocess.run([command, "serve", "--bg", "--yes", target], **kwargs)
    if completed.returncode == 0:
        return _cache_serve_state(ready=True, message="Private Tailnet HTTPS is ready")
    detail = (completed.stderr or completed.stdout or "Unable to configure Tailscale Serve").strip()
    return _cache_serve_state(ready=False, message=detail[:240])


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
            result = _build_status(_run_status(command), command)
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
    if not status.get("connected"):
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
    return dict(peer, ip=normalized)


def tailscale_allowed_hosts() -> list[str]:
    status = tailscale_status()
    hosts = [str(item) for item in status.get("ips", []) if item]
    for value in (status.get("dns_name"), status.get("hostname")):
        if value:
            hosts.append(str(value).rstrip("."))
    return hosts
