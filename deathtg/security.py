from __future__ import annotations

import ast
from dataclasses import dataclass, field

@dataclass(slots=True)
class SecurityReport:
    allowed: bool
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    def pretty(self) -> str:
        if not self.reasons:
            return "чисто, явной дичи не найдено"
        return "\n".join(f"- {reason}" for reason in self.reasons)

BLOCKED_TEXT_MARKERS = {
    "DeleteAccountRequest": "попытка удалить Telegram-аккаунт",
    "delete_account": "подозрение на удаление аккаунта",
    "account.DeleteAccount": "попытка удалить Telegram-аккаунт",
    "log_out": "попытка выйти из аккаунта/сломать сессию",
    ".session": "работа с session-файлом",
    "StringSession": "подозрительная работа со строковой сессией",
}

DANGEROUS_CALLS = {
    "eval": "eval может выполнять чужой код",
    "exec": "exec может выполнять чужой код",
    "compile": "compile может готовить чужой код к выполнению",
    "open": "работа с файлами требует проверки",
    "__import__": "динамический импорт может скрывать вредный код",
    "getattr": "может использоваться для обхода AST-защиты",
    "setattr": "может использоваться для обхода AST-защиты",
}

DANGEROUS_IMPORTS = {
    "subprocess": "запуск системных процессов",
    "shutil": "массовые операции с файлами",
    "socket": "низкоуровневая сеть/эксфильтрация",
    "ftplib": "передача данных наружу",
    "paramiko": "SSH-доступ наружу",
}

DANGEROUS_ATTRS = {
    "system": "os.system запускает команды в системе",
    "popen": "запуск системных команд",
    "Popen": "запуск системных процессов",
    "run": "subprocess.run может выполнять команды",
    "remove": "удаление файлов",
    "unlink": "удаление файлов",
    "rmtree": "удаление папок",
    "rmdir": "удаление папок",
}

FATAL_MARKERS = {
    "DeleteAccountRequest",
    "delete_account",
    "account.DeleteAccount",
    "log_out",
}

def scan_module_source(source: str) -> SecurityReport:
    report = SecurityReport(allowed=True)

    for marker, reason in BLOCKED_TEXT_MARKERS.items():
        if marker in source:
            report.score += 50 if marker in FATAL_MARKERS else 15
            report.reasons.append(reason)

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return SecurityReport(False, 999, [f"синтаксическая ошибка: {exc}"])

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in DANGEROUS_IMPORTS:
                    report.score += 25
                    report.reasons.append(DANGEROUS_IMPORTS[name])

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in DANGEROUS_CALLS:
                report.score += 20
                report.reasons.append(DANGEROUS_CALLS[func.id])
            if isinstance(func, ast.Attribute) and func.attr in DANGEROUS_ATTRS:
                report.score += 25
                report.reasons.append(DANGEROUS_ATTRS[func.attr])

    unique_reasons = []
    for reason in report.reasons:
        if reason not in unique_reasons:
            unique_reasons.append(reason)
    report.reasons = unique_reasons

    report.allowed = report.score < 50
    return report
