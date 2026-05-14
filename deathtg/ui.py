from __future__ import annotations

LOGO = """
DeathTG
Userbot Core
""".strip()

CONSOLE_BANNER = f"""
{LOGO}

DeathTG userbot core is starting...
""".strip()


def box(title: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    return f"<b>☠️ {title}</b>\n<blockquote>{body}</blockquote>"


def ok(text: str) -> str:
    return f"<b>✅ DeathTG:</b> {text}"


def warn(text: str) -> str:
    return f"<b>⚠️ DeathTG:</b> {text}"


def fail(text: str) -> str:
    return f"<b>💀 DeathTG:</b> {text}"
