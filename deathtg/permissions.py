from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import threading
from pathlib import Path
from typing import Any

from deathtg.command import Command
from deathtg.config import RUNTIME_DIR


SEC_OWNER = 1
SEC_SUDO = 2
SEC_GROUP_ADMIN = 4
SEC_GROUP_MEMBER = 8
SEC_PM = 16
SEC_EVERYONE = 32

_TOKEN_ALIASES = {
    "owner": "owner",
    "sudo": "sudo",
    "trusted": "sudo",
    "group_admin": "group_admin",
    "admin": "group_admin",
    "group_member": "group_member",
    "member": "group_member",
    "group": "group_member",
    "pm": "pm",
    "private": "pm",
    "everyone": "everyone",
    "public": "everyone",
    "all": "everyone",
}

_SEC_FROM_BITS = (
    (SEC_OWNER, "owner"),
    (SEC_SUDO, "sudo"),
    (SEC_GROUP_ADMIN, "group_admin"),
    (SEC_GROUP_MEMBER, "group_member"),
    (SEC_PM, "pm"),
    (SEC_EVERYONE, "everyone"),
)


def parse_security(security: str | int | None) -> set[str]:
    if security is None:
        return {"owner"}
    if isinstance(security, int):
        scopes = {name for bit, name in _SEC_FROM_BITS if security & bit}
        return scopes or {"owner"}
    raw = str(security).strip().lower()
    if not raw:
        return {"owner"}
    chunks = [part for part in re.split(r"[,\s|]+", raw) if part]
    scopes = {_TOKEN_ALIASES.get(chunk, "") for chunk in chunks}
    scopes.discard("")
    return scopes or {"owner"}


class SecurityManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (RUNTIME_DIR / "security.json")

    def list_sudo_users(self) -> list[int]:
        data = self._read()
        values = data.get("sudo_users", [])
        return sorted(self._integer_values(values))

    def add_sudo_user(self, user_id: int) -> None:
        data = self._read()
        users = self._integer_values(data.get("sudo_users", []))
        users.add(int(user_id))
        data["sudo_users"] = sorted(users)
        self._write(data)

    def remove_sudo_user(self, user_id: int) -> None:
        data = self._read()
        users = self._integer_values(data.get("sudo_users", []))
        users.discard(int(user_id))
        data["sudo_users"] = sorted(users)
        self._write(data)

    async def command_allowed(self, event, command: Command, owner_id: int | None) -> bool:
        actor_id = int(getattr(event, "sender_id", 0) or 0) or None
        if bool(getattr(event, "out", False)):
            return True
        if actor_id is not None and owner_id is not None and actor_id == int(owner_id):
            return True
        if actor_id is None:
            return False

        command_key = f"{command.module}.{command.name}"
        chat_id = getattr(event, "chat_id", None)

        if self._is_denied(actor_id, chat_id, command_key):
            return False
        if self._is_allowed_by_target(actor_id, chat_id, command_key):
            return True

        scopes = parse_security(command.security)
        if "everyone" in scopes:
            return True
        if "owner" in scopes and owner_id is not None and actor_id == int(owner_id):
            return True
        if "sudo" in scopes and actor_id in set(self.list_sudo_users()):
            return True

        is_private = bool(getattr(event, "is_private", False))
        is_group = bool(getattr(event, "is_group", False) or getattr(event, "is_channel", False))
        if "pm" in scopes and is_private:
            return True
        if "group_member" in scopes and is_group:
            return True
        if "group_admin" in scopes and is_group:
            if await self._is_group_admin(event, actor_id):
                return True
        return False

    async def _is_group_admin(self, event, actor_id: int) -> bool:
        try:
            perms = await event.client.get_permissions(event.chat_id, actor_id)
        except Exception:
            return False
        if getattr(perms, "is_admin", False) or getattr(perms, "is_creator", False):
            return True
        return False

    def _is_denied(self, actor_id: int, chat_id: int | None, command_key: str) -> bool:
        data = self._read()
        deny = data.get("deny", {})
        denied_users = self._merged_rule_values(deny, "users", command_key)
        denied_chats = self._merged_rule_values(deny, "chats", command_key)
        if actor_id in denied_users:
            return True
        if chat_id is not None and int(chat_id) in denied_chats:
            return True
        return False

    def _is_allowed_by_target(self, actor_id: int, chat_id: int | None, command_key: str) -> bool:
        data = self._read()
        allow = data.get("allow", {})
        allowed_users = self._merged_rule_values(allow, "users", command_key)
        allowed_chats = self._merged_rule_values(allow, "chats", command_key)
        if actor_id in allowed_users:
            return True
        if chat_id is not None and int(chat_id) in allowed_chats:
            return True
        return False

    @staticmethod
    def _merged_rule_values(rules: dict[str, Any], kind: str, command_key: str) -> set[int]:
        section = rules.get(kind, {})
        if not isinstance(section, dict):
            return set()
        values = set()
        for key in ("*", command_key):
            values.update(SecurityManager._integer_values(section.get(key, []) or []))
        return values

    @staticmethod
    def _integer_values(values) -> set[int]:
        result: set[int] = set()
        for value in values or []:
            text = str(value).strip()
            if text.isdigit():
                result.add(int(text))
        return result

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with contextlib.suppress(OSError):
                tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
            os.replace(tmp, self.path)
            with contextlib.suppress(OSError):
                self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
