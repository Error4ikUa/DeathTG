from __future__ import annotations

import argparse
import os
import secrets
import socket
import stat
from pathlib import Path
from urllib.parse import urlparse

from deathtg.config import ENV_PATH, ROOT_DIR
from deathtg.panel_access import local_network_ip, visible_panel_host

INSECURE_PASSWORDS = {"", "deathtg", "change_me_now", "admin", "password", "123456"}
INSECURE_SECRETS = {"", "change_me_long_secret", "change_me_to_random_long_string", "secret"}


def _strip(value: str | None) -> str:
    return (value or "").strip()


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _strip(os.getenv(name))
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def random_panel_password() -> str:
    return secrets.token_urlsafe(18)


def random_panel_secret() -> str:
    return secrets.token_urlsafe(48)


def secure_panel_password(preferred: str = "") -> str:
    value = _strip(preferred)
    if value.lower() in INSECURE_PASSWORDS:
        return random_panel_password()
    return value


def secure_panel_secret(preferred: str = "") -> str:
    value = _strip(preferred)
    if value.lower() in INSECURE_SECRETS:
        return random_panel_secret()
    return value


def parse_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def update_env_values(updates: dict[str, str], *, path: Path = ENV_PATH) -> dict[str, str]:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    seen: set[str] = set()
    for line in existing:
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key, _ = line.split("=", 1)
        clean_key = key.strip()
        if clean_key in updates:
            output.append(f"{clean_key}={updates[clean_key]}")
            seen.add(clean_key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    for key, value in updates.items():
        os.environ[key] = value
    return parse_env_file(path)


def ensure_server_env(*, path: Path = ENV_PATH, panel_host: str = "", panel_port: str = "", public_host: str = "", public_url: str = "") -> dict[str, str]:
    current = parse_env_file(path)
    updates: dict[str, str] = {}
    effective_public_host = public_host or current.get("PANEL_PUBLIC_HOST", "")
    effective_public_url = public_url or current.get("PANEL_PUBLIC_URL", "")
    if effective_public_host and not effective_public_url:
        effective_public_url = f"https://{effective_public_host}"
    if effective_public_url.startswith("http://"):
        effective_public_url = "https://" + effective_public_url[len("http://"):]

    password = secure_panel_password(current.get("PANEL_PASSWORD", ""))
    if current.get("PANEL_PASSWORD", "") != password:
        updates["PANEL_PASSWORD"] = password

    secret = secure_panel_secret(current.get("PANEL_SECRET", ""))
    if current.get("PANEL_SECRET", "") != secret:
        updates["PANEL_SECRET"] = secret

    default_panel_host = "0.0.0.0"
    current_panel_host = current.get("PANEL_HOST", "").strip()
    if not current_panel_host:
        resolved_panel_host = panel_host or default_panel_host
    elif current_panel_host in {"127.0.0.1", "localhost"} and not effective_public_host and not effective_public_url:
        resolved_panel_host = "0.0.0.0"
        updates["PANEL_HOST"] = resolved_panel_host
    else:
        resolved_panel_host = panel_host or current_panel_host

    desired_defaults = {
        "SESSION_NAME": current.get("SESSION_NAME", "deathtg") or "deathtg",
        "COMMAND_PREFIX": current.get("COMMAND_PREFIX", ".") or ".",
        "PANEL_HOST": resolved_panel_host,
        "PANEL_PORT": panel_port or current.get("PANEL_PORT", "8080") or "8080",
        "PANEL_SCHEME": "https" if (effective_public_host or effective_public_url) else (current.get("PANEL_SCHEME", "http") or "http"),
        "PANEL_PUBLIC_HOST": effective_public_host,
        "PANEL_PUBLIC_URL": effective_public_url,
        "PANEL_ALLOWED_HOSTS": current.get("PANEL_ALLOWED_HOSTS", ""),
        "PANEL_COOKIE_SECURE": current.get("PANEL_COOKIE_SECURE", "auto") or "auto",
        "PANEL_TRUST_PROXY": current.get("PANEL_TRUST_PROXY", "1" if (effective_public_host or effective_public_url) else "0") or "0",
        "PANEL_SHORTCUTS_ON_STARTUP": current.get("PANEL_SHORTCUTS_ON_STARTUP", "1") or "1",
        "PANEL_SHORTCUTS_MIN_INTERVAL": current.get("PANEL_SHORTCUTS_MIN_INTERVAL", "21600") or "21600",
        "BOT_TOKEN": current.get("BOT_TOKEN", ""),
        "BOT_TOKEN_HELPER": current.get("BOT_TOKEN_HELPER", ""),
        "PHONE": current.get("PHONE", ""),
        "OWNER_ID": current.get("OWNER_ID", ""),
        "API_ID": current.get("API_ID", ""),
        "API_HASH": current.get("API_HASH", ""),
    }
    for key, value in desired_defaults.items():
        if key not in current:
            updates[key] = value

    if updates:
        current = update_env_values(updates, path=path)
    return current


def _normalized_host(value: str) -> str:
    text = _strip(value)
    if not text:
        return ""
    if "://" in text:
        text = urlparse(text).hostname or ""
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if ":" in text and text.count(":") == 1:
        text = text.split(":", 1)[0]
    return text.strip().lower()


def panel_allowed_hosts() -> list[str]:
    hosts = {"127.0.0.1", "localhost"}
    with_hostname = _normalized_host(socket.gethostname())
    if with_hostname:
        hosts.add(with_hostname)
    env = parse_env_file()
    for raw in (
        env.get("PANEL_HOST", ""),
        env.get("PANEL_PUBLIC_HOST", ""),
        env.get("PANEL_PUBLIC_URL", ""),
        os.getenv("PANEL_HOST", ""),
        os.getenv("PANEL_PUBLIC_HOST", ""),
        os.getenv("PANEL_PUBLIC_URL", ""),
    ):
        host = _normalized_host(raw)
        if host:
            hosts.add(host)
    for raw in (visible_panel_host(), local_network_ip()):
        host = _normalized_host(raw)
        if host:
            hosts.add(host)
    extra = _strip(env.get("PANEL_ALLOWED_HOSTS", "") or os.getenv("PANEL_ALLOWED_HOSTS", ""))
    if extra:
        for item in extra.split(","):
            host = _normalized_host(item)
            if host:
                hosts.add(host)
    return sorted(hosts)


def panel_cookie_secure() -> bool:
    raw = _strip(os.getenv("PANEL_COOKIE_SECURE"))
    if raw and raw.lower() not in {"auto", "default"}:
        return raw.lower() in {"1", "true", "yes", "on"}
    public_url = _strip(os.getenv("PANEL_PUBLIC_URL"))
    if public_url:
        return public_url.lower().startswith("https://")
    scheme = _strip(os.getenv("PANEL_SCHEME")).lower()
    return scheme == "https"


def panel_trust_proxy() -> bool:
    return _bool_env("PANEL_TRUST_PROXY", default=False)


def render_systemd_service(project_dir: Path, python_path: Path) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=DeathTG full stack",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={project_dir}",
            f"ExecStart={python_path} dtg.py",
            "Restart=always",
            "RestartSec=5",
            "TimeoutStopSec=20",
            "Environment=PYTHONUNBUFFERED=1",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )


def render_nginx_config(server_name: str, *, panel_port: str = "8080") -> str:
    upstream = f"http://127.0.0.1:{panel_port}"
    return "\n".join(
        [
            "server {",
            "    listen 80;",
            f"    server_name {server_name};",
            "",
            "    client_max_body_size 25m;",
            "    proxy_read_timeout 300s;",
            "    proxy_send_timeout 300s;",
            "",
            "    location / {",
            f"        proxy_pass {upstream};",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Host $host;",
            "        proxy_set_header X-Real-IP $remote_addr;",
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            "        proxy_set_header X-Forwarded-Proto $scheme;",
            "        proxy_set_header Upgrade $http_upgrade;",
            "        proxy_set_header Connection \"upgrade\";",
            "        add_header X-Frame-Options DENY always;",
            "        add_header X-Content-Type-Options nosniff always;",
            "        add_header Referrer-Policy no-referrer always;",
            "    }",
            "}",
            "",
        ]
    )


def render_caddy_config(server_name: str, *, panel_port: str = "8080") -> str:
    return "\n".join(
        [
            f"{server_name} {{",
            f"    reverse_proxy 127.0.0.1:{panel_port}",
            "    encode gzip zstd",
            "    header {",
            "        X-Frame-Options DENY",
            "        X-Content-Type-Options nosniff",
            "        Referrer-Policy no-referrer",
            "        Permissions-Policy \"camera=(), microphone=(), geolocation=()\"",
            "    }",
            "}",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate secure DeathTG server defaults and deploy files.")
    parser.add_argument("--write-env", action="store_true", help="Fill missing or insecure env values.")
    parser.add_argument("--panel-host", default="0.0.0.0")
    parser.add_argument("--panel-port", default="8080")
    parser.add_argument("--public-host", default="")
    parser.add_argument("--public-url", default="")
    parser.add_argument("--service-file", default="")
    parser.add_argument("--nginx-file", default="")
    parser.add_argument("--caddy-file", default="")
    parser.add_argument("--server-name", default="")
    args = parser.parse_args()

    env = ensure_server_env(
        panel_host=args.panel_host,
        panel_port=args.panel_port,
        public_host=args.public_host,
        public_url=args.public_url,
    ) if args.write_env else parse_env_file()

    if args.service_file:
        service_path = Path(args.service_file)
        service_path.parent.mkdir(parents=True, exist_ok=True)
        python_path = ROOT_DIR / ".venv" / "bin" / "python"
        if not python_path.exists():
            python_path = Path(os.sys.executable)
        service_path.write_text(
            render_systemd_service(ROOT_DIR, python_path),
            encoding="utf-8",
        )

    if args.nginx_file:
        nginx_path = Path(args.nginx_file)
        nginx_path.parent.mkdir(parents=True, exist_ok=True)
        server_name = args.server_name or args.public_host or _normalized_host(args.public_url) or "_"
        nginx_path.write_text(
            render_nginx_config(server_name, panel_port=env.get("PANEL_PORT", args.panel_port)),
            encoding="utf-8",
        )

    if args.caddy_file:
        caddy_path = Path(args.caddy_file)
        caddy_path.parent.mkdir(parents=True, exist_ok=True)
        server_name = args.server_name or args.public_host or _normalized_host(args.public_url) or "_"
        caddy_path.write_text(
            render_caddy_config(server_name, panel_port=env.get("PANEL_PORT", args.panel_port)),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
