from __future__ import annotations

import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from deathtg.bot_health import sync_bot_checks
from deathtg.config import DOWNLOADS_DIR, ENV_PATH, MODULES_DIR, ROOT_DIR, RUNTIME_DIR
from deathtg.config_manager import load_managed_config, sync_config
from deathtg.server_bootstrap import ensure_server_env, parse_env_file, update_env_values
from deathtg.state_db import ensure_state_db, event, set_health, sync_settings_from_config

LOGS_DIR = ROOT_DIR / "logs"
ASSETS_DIR = ROOT_DIR / "assets"
CACHE_DIR = RUNTIME_DIR / "cache"
PANEL_RUNTIME_DIR = RUNTIME_DIR / "panel"
STARTUP_REPORT = RUNTIME_DIR / "startup_report.json"

REQUIRED_DIRS = (MODULES_DIR, DOWNLOADS_DIR, RUNTIME_DIR, LOGS_DIR, CACHE_DIR, PANEL_RUNTIME_DIR)
REQUIRED_IMPORTS = ("dotenv", "uvicorn", "telethon", "fastapi", "jinja2")


@dataclass(slots=True)
class StartupIssue:
    level: str
    code: str
    message: str
    repair: str = ""


@dataclass(slots=True)
class StartupReport:
    ok: bool
    mode: str
    root: str
    python: str
    issues: list[StartupIssue]

    def save(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STARTUP_REPORT.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _env_truthy(name: str) -> bool:
    return (os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"})


def _missing_imports(names: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for name in names:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    return missing


def _session_exists(env: dict[str, str]) -> bool:
    session_name = (env.get("SESSION_NAME") or "deathtg").strip() or "deathtg"
    return (ROOT_DIR / f"{session_name}.session").exists()


def ensure_runtime_layout() -> None:
    for path in REQUIRED_DIRS:
        path.mkdir(parents=True, exist_ok=True)
    for log_name in ("dtg.log", "panel.log", "modules.log", "health.log", "startup.log"):
        log_file = LOGS_DIR / log_name
        if not log_file.exists():
            log_file.touch()


def run_preflight(*, repair: bool = False, safe: bool = False, no_panel: bool = False, no_modules: bool = False) -> StartupReport:
    issues: list[StartupIssue] = []
    ensure_runtime_layout()
    ensure_state_db()

    if repair or not ENV_PATH.exists():
        ensure_server_env(path=ENV_PATH)

    config_status = sync_config(repair=repair)
    managed_config = load_managed_config()
    sync_settings_from_config(managed_config)
    set_health("config", "ok" if config_status.ok else "error", "Config manager validation", asdict(config_status))
    bot_checks = sync_bot_checks()

    env = parse_env_file(ENV_PATH)

    if sys.version_info < (3, 10):
        issues.append(StartupIssue("error", "python_old", "Python 3.10+ is required", "Install newer Python"))

    missing = _missing_imports(REQUIRED_IMPORTS)
    if missing:
        issues.append(
            StartupIssue(
                "error",
                "missing_requirements",
                "Missing Python packages: " + ", ".join(missing),
                "Run: python -m pip install -r requirements.txt",
            )
        )

    if not ENV_PATH.exists():
        issues.append(StartupIssue("warning", "env_missing", ".env was missing and has been created", "Open setup page and fill API_ID/API_HASH"))

    api_id = (env.get("API_ID") or os.getenv("API_ID", "")).strip()
    api_hash = (env.get("API_HASH") or os.getenv("API_HASH", "")).strip()
    if not api_id or not api_hash:
        issues.append(StartupIssue("setup", "telegram_api_missing", "API_ID/API_HASH are not configured", "Open setup page"))
    elif not api_id.isdigit():
        issues.append(StartupIssue("error", "api_id_invalid", "API_ID must be numeric", "Fix .env"))

    login_pending = (env.get("LOGIN_PENDING") or os.getenv("LOGIN_PENDING", "0")).strip().lower() in {"1", "true", "yes", "on"}
    if api_id and api_hash and not login_pending and not _session_exists(env):
        issues.append(StartupIssue("setup", "session_missing", "Telegram session file is missing", "Finish login from web setup"))

    for check in bot_checks:
        if check.required and check.status != "configured":
            issues.append(StartupIssue("setup", f"bot_{check.bot_key}_{check.status}", f"{check.label}: {check.error or check.status}", check.recovery))

    panel_port = (env.get("PANEL_PORT") or "8080").strip()
    try:
        port = int(panel_port)
        if port < 1 or port > 65535:
            raise ValueError
    except ValueError:
        issues.append(StartupIssue("warning", "panel_port_invalid", f"Invalid PANEL_PORT={panel_port}; resetting to 8080", "Auto repair"))
        update_env_values({"PANEL_PORT": "8080"}, path=ENV_PATH)

    if safe:
        os.environ["DTG_SAFE_MODE"] = "1"
        os.environ["DTG_NO_MODULES"] = "1"
    if no_modules:
        os.environ["DTG_NO_MODULES"] = "1"
    if no_panel:
        os.environ["DTG_NO_PANEL"] = "1"

    mode_parts = []
    if repair:
        mode_parts.append("repair")
    if safe:
        mode_parts.append("safe")
    if no_panel:
        mode_parts.append("no-panel")
    if no_modules:
        mode_parts.append("no-modules")
    mode = "+".join(mode_parts) if mode_parts else "normal"

    fatal = any(issue.level == "error" for issue in issues)
    report = StartupReport(
        ok=not fatal,
        mode=mode,
        root=str(ROOT_DIR),
        python=sys.version.split()[0],
        issues=issues,
    )
    report.save()
    set_health("startup", "ok" if report.ok else "error", f"Startup preflight: {mode}", asdict(report))
    event(
        "startup.preflight",
        f"Startup preflight finished: {mode}",
        level="info" if report.ok else "error",
        entity_type="startup",
        entity_id=mode,
        details=asdict(report),
    )
    return report


def print_report(report: StartupReport) -> None:
    print("Startup preflight:", "OK" if report.ok else "ERROR")
    print(f"Mode: {report.mode}")
    print(f"Root: {report.root}")
    if not report.issues:
        print("Checks: all green")
        return
    for issue in report.issues:
        prefix = issue.level.upper()
        print(f"[{prefix}] {issue.code}: {issue.message}")
        if issue.repair:
            print(f"        fix: {issue.repair}")


def ready_to_start_userbot() -> bool:
    env = parse_env_file(ENV_PATH)
    if _env_truthy("DTG_SAFE_MODE"):
        return False
    if not (env.get("API_ID") or "").strip() or not (env.get("API_HASH") or "").strip():
        return False
    if (env.get("LOGIN_PENDING") or "0").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    return _session_exists(env)
