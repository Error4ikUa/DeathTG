from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import time
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from urllib.parse import urlparse

from deathtg.config import RUNTIME_DIR

GRANTS_PATH = RUNTIME_DIR / "panel_device_grants.json"
DEVICES_PATH = RUNTIME_DIR / "panel_devices.json"
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
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
        if host and host not in {"127.0.0.1", "0.0.0.0"}:
            return host
    except Exception:
        pass
    return ""


def effective_panel_bind_host() -> str:
    configured = _env("PANEL_HOST")
    if configured:
        return configured
    if _env("PANEL_PUBLIC_URL") or _env("PANEL_PUBLIC_HOST"):
        return "0.0.0.0"
    return "0.0.0.0"


def visible_panel_host() -> str:
    host = _env("PANEL_PUBLIC_HOST") or effective_panel_bind_host()
    if host in {"0.0.0.0", "::"}:
        lan_ip = local_network_ip()
        return lan_ip or "127.0.0.1"
    return host or "127.0.0.1"


def panel_base_url() -> str:
    full = _env("PANEL_PUBLIC_URL")
    if full:
        return full.rstrip("/")
    scheme = _env("PANEL_SCHEME", "http") or "http"
    host = visible_panel_host()
    port = _env("PANEL_PORT", "8080") or "8080"
    if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


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


def issue_device_grant(device_name: str, *, ttl_seconds: int = DEFAULT_GRANT_TTL, created_by: str = "panel") -> str:
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
    return f"{panel_base_url()}/grant/{token}"


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
