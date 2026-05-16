from __future__ import annotations

import ast
from dataclasses import dataclass, field
from urllib.parse import urlparse


OFFICIAL_REPO_PREFIXES = (
    "/error4ikua/dtg_modules/",
    "/repos/error4ikua/dtg_modules/",
)


@dataclass(slots=True)
class SecurityFinding:
    line: int
    reason: str
    score: int
    code: str = ""


@dataclass(slots=True)
class SecurityReport:
    allowed: bool
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    findings: list[SecurityFinding] = field(default_factory=list)
    trusted: bool = False
    severity: str = "clean"
    verdict: str = "ALLOWED"

    def pretty(self) -> str:
        if not self.findings:
            if self.trusted:
                return "official DTG module, source is trusted"
            return "clean, no obvious dangerous code found"
        lines = []
        for finding in self.findings:
            where = f"line {finding.line}: " if finding.line else ""
            code = f" | {finding.code.strip()}" if finding.code else ""
            lines.append(f"- {where}{finding.reason}{code}")
        return "\n".join(lines)


RAW_MARKERS: tuple[tuple[str, str, int], ...] = (
    ("DeleteAccountRequest", "attempt to delete Telegram account", 120),
    ("account.DeleteAccount", "attempt to delete Telegram account", 120),
    ("delete_account", "possible account deletion", 110),
    ("ResetAuthorizationsRequest", "resets all active sessions", 100),
    ("ResetAuthorizationRequest", "resets an active session", 80),
    ("LogOutRequest", "logs out through Telegram API", 100),
    ("log_out(", "attempt to log out account", 100),
    ("TerminateAllSessions", "attempt to terminate sessions", 100),
    ("DeleteHistoryRequest", "mass chat history deletion", 75),
    ("DeleteMessagesRequest", "message deletion through Telegram API", 70),
    ("DeleteUserHistoryRequest", "user history deletion", 70),
    ("DeleteParticipantHistoryRequest", "participant history deletion", 70),
    ("DeleteChannelRequest", "channel deletion", 95),
    ("LeaveChannelRequest", "leaves a channel", 65),
    ("EditBannedRequest", "can ban or mute chat members", 70),
    ("KickFromChannelRequest", "can kick channel members", 80),
    ("StringSession.save", "exports or reads a string session", 95),
    ("session.save(", "exports or reads a session", 90),
    ("ExportAuthorizationRequest", "exports authorization", 100),
    (".session", "touches session files", 70),
)

IMPORT_SCORES = {
    "subprocess": ("starts system processes", 35),
    "socket": ("low-level network access or exfiltration risk", 30),
    "ftplib": ("external data transfer", 30),
    "paramiko": ("external SSH access", 35),
    "shutil": ("broad file operations", 18),
}

CALL_SCORES = {
    "eval": ("eval can execute arbitrary code", 50),
    "exec": ("exec can execute arbitrary code", 50),
    "compile": ("compile can prepare dynamic code execution", 30),
    "__import__": ("dynamic import can hide unsafe code", 25),
    "open": ("file access needs review", 10),
    "getattr": ("dynamic attribute access", 4),
    "setattr": ("dynamic attribute mutation", 4),
}

ATTR_SCORES = {
    "system": ("os.system executes shell commands", 45),
    "popen": ("starts system commands", 45),
    "Popen": ("starts system processes", 45),
    "run": ("subprocess.run can execute commands", 35),
    "remove": ("file deletion", 18),
    "unlink": ("file deletion", 18),
    "rmtree": ("directory deletion", 40),
    "rmdir": ("directory deletion", 22),
    "delete_dialog": "chat deletion through client",
    "delete_messages": "message deletion through client",
    "delete_history": "history deletion through client",
    "kick_participant": "kicks chat members through client",
    "edit_banned": "bans or mutes members through client",
    "log_out": "logs out account",
}

ATTR_SCORES = {
    key: value if isinstance(value, tuple) else (value, 80 if key != "log_out" else 100)
    for key, value in ATTR_SCORES.items()
}

DANGEROUS_CALL_NAMES = {
    "DeleteAccountRequest": ("attempt to delete Telegram account", 120),
    "ResetAuthorizationsRequest": ("resets all active sessions", 100),
    "ResetAuthorizationRequest": ("resets an active session", 80),
    "LogOutRequest": ("logs out through Telegram API", 100),
    "DeleteHistoryRequest": ("mass chat history deletion", 75),
    "DeleteMessagesRequest": ("message deletion through Telegram API", 70),
    "DeleteUserHistoryRequest": ("user history deletion", 70),
    "DeleteParticipantHistoryRequest": ("participant history deletion", 70),
    "DeleteChannelRequest": ("channel deletion", 95),
    "LeaveChannelRequest": ("leaves a channel", 65),
    "KickFromChannelRequest": ("kicks channel members", 80),
    "EditBannedRequest": ("can ban or mute channel members", 70),
    "ExportAuthorizationRequest": ("exports authorization", 100),
}


def is_trusted_module_link(link: str) -> bool:
    raw = (link or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    if host not in {"raw.githubusercontent.com", "github.com", "api.github.com"}:
        return False
    return any(path.startswith(prefix) for prefix in OFFICIAL_REPO_PREFIXES)


def _normalize_source(source: str) -> str:
    cleaned = source or ""
    for token in ("\ufeff", "\u00a0", "\u200b", "\u200c", "\u200d", "\u2060"):
        cleaned = cleaned.replace(token, " ")
    return cleaned


def _append(report: SecurityReport, reason: str, score: int, *, line: int = 0, code: str = "") -> None:
    report.score += score
    label = f"line {line}: {reason}" if line else reason
    if label not in report.reasons:
        report.reasons.append(label)
    if not any(item.line == line and item.reason == reason and item.code == code.strip() for item in report.findings):
        report.findings.append(SecurityFinding(line=line, reason=reason, score=score, code=code.strip()))


def _line_for_marker(source: str, marker: str) -> tuple[int, str]:
    marker_lower = marker.lower()
    for idx, line in enumerate(source.splitlines(), 1):
        if marker_lower in line.lower():
            return idx, line
    return 0, ""


def _raw_scan(report: SecurityReport, source: str) -> None:
    lowered = source.lower()
    for marker, reason, score in RAW_MARKERS:
        if marker.lower() in lowered:
            line, code = _line_for_marker(source, marker)
            _append(report, reason, score, line=line, code=code)


def _source_line(source_lines: list[str], node: ast.AST) -> tuple[int, str]:
    line = int(getattr(node, "lineno", 0) or 0)
    code = source_lines[line - 1] if line and line <= len(source_lines) else ""
    return line, code


def _scan_tree(report: SecurityReport, tree: ast.AST, source_lines: list[str]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in IMPORT_SCORES:
                    reason, score = IMPORT_SCORES[root]
                    line, code = _source_line(source_lines, node)
                    _append(report, reason, score, line=line, code=code)

        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in IMPORT_SCORES:
                reason, score = IMPORT_SCORES[root]
                line, code = _source_line(source_lines, node)
                _append(report, reason, score, line=line, code=code)

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in CALL_SCORES:
                    reason, score = CALL_SCORES[func.id]
                    line, code = _source_line(source_lines, node)
                    _append(report, reason, score, line=line, code=code)
                if func.id in DANGEROUS_CALL_NAMES:
                    reason, score = DANGEROUS_CALL_NAMES[func.id]
                    line, code = _source_line(source_lines, node)
                    _append(report, reason, score, line=line, code=code)
            elif isinstance(func, ast.Attribute):
                if func.attr in ATTR_SCORES:
                    reason, score = ATTR_SCORES[func.attr]
                    line, code = _source_line(source_lines, node)
                    _append(report, reason, score, line=line, code=code)
                if func.attr in DANGEROUS_CALL_NAMES:
                    reason, score = DANGEROUS_CALL_NAMES[func.attr]
                    line, code = _source_line(source_lines, node)
                    _append(report, reason, score, line=line, code=code)


def _finalize(report: SecurityReport) -> SecurityReport:
    if report.trusted:
        report.allowed = True
        report.verdict = "VERIFIED"
        report.severity = "trusted" if report.score < 70 else "trusted-risk"
        return report

    if report.score >= 70:
        report.allowed = False
        report.verdict = "BLOCKED"
        report.severity = "danger"
    elif report.score >= 25:
        report.allowed = True
        report.verdict = "WARN"
        report.severity = "warning"
    else:
        report.allowed = True
        report.verdict = "ALLOWED"
        report.severity = "clean"
    return report


def scan_module_source(source: str, *, trusted: bool = False) -> SecurityReport:
    source = _normalize_source(source)
    report = SecurityReport(allowed=True, trusted=trusted)
    _raw_scan(report, source)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        lines = source.splitlines()
        line = int(getattr(exc, "lineno", 0) or 0)
        code = lines[line - 1] if line and line <= len(lines) else ""
        _append(report, f"syntax error: {exc}", 999, line=line, code=code)
        if trusted:
            report.allowed = True
            report.verdict = "VERIFIED"
            report.severity = "trusted-risk"
        else:
            report.allowed = False
            report.verdict = "BLOCKED"
            report.severity = "danger"
        return report

    _scan_tree(report, tree, source.splitlines())
    return _finalize(report)
