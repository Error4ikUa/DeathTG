from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from deathtg.assets import resolve_module_entry
from deathtg.config import MODULES_DIR
from deathtg.security import SecurityReport, scan_module_source
from deathtg.state_db import set_antivirus_report, set_health, upsert


def classify_report(report: SecurityReport) -> str:
    if report.trusted and report.allowed:
        return "trusted"
    if not report.allowed:
        return "blocked"
    if report.score >= 70:
        return "dangerous"
    if report.score >= 25:
        return "suspicious"
    return "clean"


def scan_source(module_key: str, source: str, *, trusted: bool = False) -> SecurityReport:
    report = scan_module_source(source, trusted=trusted)
    findings = [asdict(item) for item in report.findings]
    set_antivirus_report(
        module_key,
        verdict=report.verdict,
        severity=report.severity,
        score=report.score,
        allowed=report.allowed,
        trusted=report.trusted,
        findings=findings,
        pretty=report.pretty(),
    )
    status = classify_report(report)
    upsert(
        "modules",
        "module_key",
        module_key,
        {
            "antivirus_status": status,
            "status": "blocked" if not report.allowed else "installed",
            "error": "" if report.allowed else report.pretty(),
        },
        preserve_existing=True,
        event_type="antivirus.module_status",
    )
    return report


def scan_module_path(path: Path, *, trusted: bool = False, module_key: str | None = None) -> SecurityReport | None:
    entry = resolve_module_entry(path, module_key)
    if not entry or not entry.exists() or entry.suffix.lower() != ".py":
        return None
    key = module_key or (path.stem if path.is_file() else path.name)
    source = entry.read_text(encoding="utf-8")
    return scan_source(key, source, trusted=trusted)


def scan_all_modules() -> dict[str, Any]:
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    scanned = 0
    blocked = 0
    suspicious = 0
    errors: list[str] = []
    for path in sorted(MODULES_DIR.iterdir(), key=lambda item: item.name.lower()):
        if path.name.startswith("_"):
            continue
        if path.is_file() and path.suffix.lower() != ".py":
            continue
        try:
            report = scan_module_path(path)
            if not report:
                continue
            scanned += 1
            status = classify_report(report)
            if status == "blocked":
                blocked += 1
            elif status in {"suspicious", "dangerous"}:
                suspicious += 1
        except Exception as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    health = "ok" if not blocked and not errors else "warning"
    set_health(
        "antivirus",
        health,
        f"Antivirus scanned {scanned} module(s), blocked {blocked}, suspicious {suspicious}",
        {"scanned": scanned, "blocked": blocked, "suspicious": suspicious, "errors": errors},
    )
    return {"scanned": scanned, "blocked": blocked, "suspicious": suspicious, "errors": errors}
