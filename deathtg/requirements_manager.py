from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Iterable

from deathtg.state_db import connect, ensure_state_db, set_health, set_module_requirement_status

PACKAGE_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")
SAFE_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?"
    r"(?:\s*(?:==|~=|>=|<=|!=|>|<)\s*[A-Za-z0-9_.!+*-]+"
    r"(?:\s*,\s*(?:==|~=|>=|<=|!=|>|<)\s*[A-Za-z0-9_.!+*-]+)*)?$"
)
INTERNAL_PACKAGES = {
    "deathtg",
    "death-tg",
    "hikka",
    "hikariatama",
}


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


def safe_requirements(requirements: Iterable[str]) -> tuple[list[str], list[str]]:
    """Return installable PyPI requirements and ignored internal entries.

    Third-party modules often import DeathTG/Hikka compatibility namespaces.
    An ImportError for one of those namespaces must never become
    ``pip install deathtg``: it is an application compatibility problem, not a
    missing package from PyPI.
    """

    safe: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for raw in requirements:
        requirement = str(raw or "").split("#", 1)[0].strip()
        if not requirement:
            continue
        project = package_name(requirement)
        internal = project in INTERNAL_PACKAGES
        if internal or not SAFE_REQUIREMENT_RE.fullmatch(requirement):
            if requirement not in skipped:
                skipped.append(requirement)
            continue
        key = requirement.lower()
        if key not in seen:
            safe.append(requirement)
            seen.add(key)
    return safe, skipped


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
        set_module_requirement_status(mod, req, installed, "" if installed else error)
    missing = [item for item in statuses if not item.installed]
    set_health(
        "requirements",
        "ok" if not missing else "warning",
        "All module requirements are installed" if not missing else f"Missing requirements: {len(missing)}",
        {"requirements": [asdict(item) for item in statuses]},
    )
    return statuses


def install_requirements(requirements: Iterable[str]) -> dict[str, object]:
    reqs, skipped = safe_requirements(requirements)
    if not reqs:
        return {
            "ok": True,
            "installed": [],
            "skipped": skipped,
            "message": "Nothing to install",
        }
    cmd = [sys.executable, "-m", "pip", "install", *reqs]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "installed": reqs if ok else [],
        "skipped": skipped,
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
