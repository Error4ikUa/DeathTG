from __future__ import annotations

import os
import sys

LOGO = """
██████╗░███████╗░█████╗░████████╗██╗░░██╗  ████████╗░██████╗░
██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██║░░██║  ╚══██╔══╝██╔════╝░
██║░░██║█████╗░░███████║░░░██║░░░███████║  ░░░██║░░░██║░░██╗░
██║░░██║██╔══╝░░██╔══██║░░░██║░░░██╔══██║  ░░░██║░░░██║░░╚██╗
██████╔╝███████╗██║░░██║░░░██║░░░██║░░██║  ░░░██║░░░╚██████╔╝
╚═════╝░╚══════╝╚═╝░░╚═╝░░░╚═╝░░░╚═╝░░╚═╝  ░░░╚═╝░░░░╚═════╝░
""".strip()

RESET = "\033[0m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
BOLD = "\033[1m"


def supports_color() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def colorize(text: str, *codes: str) -> str:
    if not supports_color():
        return text
    prefix = "".join(codes)
    return f"{prefix}{text}{RESET}"


def welcome_block() -> str:
    return "\n".join(
        [
            colorize("Welcome", BOLD, GREEN) + " to " + colorize("DeathTG", BOLD, MAGENTA),
            colorize("Secure", CYAN) + " Telegram userbot " + colorize("platform", YELLOW),
        ]
    )


CONSOLE_BANNER = f"""
{LOGO}

{welcome_block()}
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
