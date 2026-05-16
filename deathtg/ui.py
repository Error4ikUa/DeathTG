from __future__ import annotations

LOGO = """
██████╗░███████╗░█████╗░████████╗██╗░░██╗  ████████╗░██████╗░
██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██║░░██║  ╚══██╔══╝██╔════╝░
██║░░██║█████╗░░███████║░░░██║░░░███████║  ░░░██║░░░██║░░██╗░
██║░░██║██╔══╝░░██╔══██║░░░██║░░░██╔══██║  ░░░██║░░░██║░░╚██╗
██████╔╝███████╗██║░░██║░░░██║░░░██║░░██║  ░░░██║░░░╚██████╔╝
╚═════╝░╚══════╝╚═╝░░╚═╝░░░╚═╝░░░╚═╝░░╚═╝  ░░░╚═╝░░░░╚═════╝░
""".strip()

CONSOLE_BANNER = f"""
{LOGO}

DeathTG secure userbot platform is starting...
""".strip()


def box(title: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    return f"<b>[ DeathTG ] {title}</b>\n<blockquote>{body}</blockquote>"


def ok(text: str) -> str:
    return f"<b>[ OK ] DeathTG:</b> {text}"


def warn(text: str) -> str:
    return f"<b>[ WARN ] DeathTG:</b> {text}"


def fail(text: str) -> str:
    return f"<b>[ FAIL ] DeathTG:</b> {text}"
