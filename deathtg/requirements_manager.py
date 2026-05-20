from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from deathtg.state_db import connect, ensure_state_db, set_health, upsert

PACKAGE_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


@dataclass(slots=True)
class RequirementStatus:
    module_key: str
    requirement: str
    package: str
    installed: bool
    installed_version: str = ""
    error: str = ""


def package_name(requirement: str) -> str:
    text = requirement.strip()
    match = PACKAGE_NAME_RE.match(text)
    return match.group(1).replace("_", "-").lower() if match else text.lower()


def is_requirement_installed(requirement: str) -> tuple[bool, str, str]:
    pkg = package_name(requirement)
    if not pkg:
        return False, "", "empty requirement"
    candidates = [pkg, pkg.replace("-", "_")]
    for candidate in candidates:
        try:
            version = importlib.metadata.version(candidate)
            return True, version, ""
        except importlib.metadata.PackageNotFoundError:
            continue
        except Exception as exc:
            return False, "", str(exc)
    return False, "", "not installed"


def all_requirements() -> list[tuple[str, str]]:
    ensure_state_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT module_key, requirement
            FROM module_requirements
            ORDER BY module_key, requirement
            """
        ).fetchall()
    return [(str(row["module_key"]), str(row["requirement"])) for row in rows]


def check_requirements(module_key: str | None = None) -> list[RequirementStatus]:
    ensure_state_db()
    with connect() as conn:
        if module_key:
            rows = conn.execute(
                "SELECT module_key, requirement FROM module_requirements WHERE module_key=? ORDER BY requirement",
                (module_key,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT module_key, requirement FROM module_requirements ORDER BY module_key, requirement"
            ).fetchall()

    statuses: list[RequirementStatus] = []
    for row in rows:
        mod = str(row["module_key"])
        req = str(row["requirement"])
        installed, version, error = is_requirement_installed(req)
        status = RequirementStatus(
            module_key=mod,
            requirement=req,
            package=package_name(req),
            installed=installed,
            installed_version=version,
            error="" if installed else error,
        )
        statuses.append(status)
        upsert(
            "module_requirements",
            "module_key",
            mod,
            {
                "requirement": req,
                "installed": 1 if installed else 0,
                "error": "" if installed else error,
            },
            preserve_existing=False,
            event_type="requirement.check",
        )
    missing = [item for item in statuses if not item.installed]
    set_health(
        "requirements",
        "ok" if not missing else "warning",
        "All module requirements are installed" if not missing else f"Missing requirements: {len(missing)}",
        {"requirements": [asdict(item) for item in statuses]},
    )
    return statuses


def install_requirements(requirements: Iterable[str]) -> dict[str, object]:
    reqs = [req.strip() for req in requirements if str(req).strip()]
    if not reqs:
        return {"ok": True, "installed": [], "message": "Nothing to install"}
    cmd = [sys.executable, "-m", "pip", "install", *reqs]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "installed": reqs if ok else [],
        "returncode": proc.returncode,
        "stdout": proc.stdout[-5000:],
        "stderr": proc.stderr[-5000:],
        "message": proc.stdout[-1000:] if ok else proc.stderr[-1000:],
    }


def install_missing_requirements(module_key: str | None = None) -> dict[str, object]:
    statuses = check_requirements(module_key)
    missing = [item.requirement for item in statuses if not item.installed]
    result = install_requirements(missing)
    check_requirements(module_key)
    set_health(
        "requirements.install",
        "ok" if result.get("ok") else "error",
        str(result.get("message") or "Requirements install finished")[-500:],
        result,
    )
    return result


def requirements_summary() -> dict[str, int]:
    statuses = check_requirements()
    total = len(statuses)
    installed = sum(1 for item in statuses if item.installed)
    missing = total - installed
    return {"total": total, "installed": installed, "missing": missing}
