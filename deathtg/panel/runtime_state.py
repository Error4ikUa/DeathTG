from __future__ import annotations

import json

from deathtg.config import RUNTIME_DIR
from deathtg.panel.clean_core import avatar_url


def read_profile() -> dict[str, str]:
    avatar = avatar_url()
    path = RUNTIME_DIR / "profile.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "name": data.get("name") or "DeathTG User",
                "username": data.get("username") or "",
                "id": str(data.get("id") or "unknown"),
                "ok": data.get("ok") or "1",
                "avatar": avatar,
            }
        except Exception:
            pass
    return {"name": "DeathTG User", "username": "not connected", "id": "unknown", "ok": "0", "avatar": avatar}
