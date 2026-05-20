from __future__ import annotations

from typing import Any

from deathtg.state_db import set_health, set_resource


def sync_startup_resources(status: dict[str, Any]) -> None:
    channels = [item for item in list(status.get("channels") or []) if isinstance(item, dict)]
    folder = status.get("folder") if isinstance(status.get("folder"), dict) else {}
    shortcuts = status.get("shortcuts") if isinstance(status.get("shortcuts"), dict) else {}

    errors: list[str] = []

    for channel in channels:
        username = str(channel.get("username") or "").strip()
        key = f"channel.{username or len(errors)}"
        ok = bool(channel.get("joined")) and not channel.get("error")
        error = str(channel.get("error") or "")
        if error:
            errors.append(f"{username}: {error}")
        set_resource(
            key,
            "channel",
            title=str(channel.get("title") or username),
            username=username,
            status="ok" if ok else "error",
            error=error,
            metadata=channel,
        )

    if folder:
        ok = bool(folder.get("ok"))
        error = str(folder.get("error") or "")
        if error:
            errors.append(f"folder: {error}")
        set_resource(
            "folder.deathtg",
            "folder",
            title=str(folder.get("name") or "DeathTG"),
            status="ok" if ok else "error",
            error=error,
            metadata=folder,
        )

    if shortcuts:
        ok = bool(shortcuts.get("sent")) or not shortcuts.get("error")
        error = str(shortcuts.get("error") or "")
        if error and not ok:
            errors.append(f"shortcuts: {error}")
        set_resource(
            "shortcut.owner_panel",
            "shortcut",
            title="Owner panel shortcut",
            status="ok" if ok else "warning",
            error=error,
            metadata=shortcuts,
        )

    set_health(
        "telegram_resources",
        "ok" if not errors else "warning",
        "Telegram resources checked" if not errors else "; ".join(errors[:5]),
        {"channels": channels, "folder": folder, "shortcuts": shortcuts, "errors": errors},
    )
