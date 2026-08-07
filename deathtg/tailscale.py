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
    host = dns_name or next((ip for ip in own_ips if ":" not in ip), "") or (own_ips[0] if own_ips else "")
    port = os.getenv("PANEL_PORT", "8080").strip() or "8080"
    url_host = f"[{host}]" if ":" in host else host
    url = f"http://{url_host}:{port}" if connected and host else ""
    return {
        "available": True,
        "connected": connected,
        "trusted_access": _enabled(),
        "backend_state": backend_state,
        "hostname": hostname,
        "dns_name": dns_name,
        "ips": own_ips,
        "peer_ips": peer_ips,
        "url": url,
        "command": command,
        "message": "Tailnet access is ready" if connected else f"Tailscale state: {backend_state}",
    }


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
