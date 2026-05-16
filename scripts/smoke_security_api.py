from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deathtg.command import Command
from deathtg.permissions import SecurityManager, parse_security


async def noop(event, args):
    return None


async def main() -> None:
    assert parse_security(None) == {"owner"}
    assert parse_security("owner|sudo") == {"owner", "sudo"}
    assert "everyone" in parse_security("public")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "security.json"
        manager = SecurityManager(path)
        manager.add_sudo_user(1001)
        manager.add_sudo_user(1002)
        assert manager.list_sudo_users() == [1001, 1002]
        manager.remove_sudo_user(1002)
        assert manager.list_sudo_users() == [1001]

        outgoing_event = SimpleNamespace(
            sender_id=777,
            out=True,
            chat_id=1,
            is_private=False,
            is_group=True,
            is_channel=False,
            client=None,
        )
        incoming_pm = SimpleNamespace(
            sender_id=1001,
            out=False,
            chat_id=2,
            is_private=True,
            is_group=False,
            is_channel=False,
            client=None,
        )

        owner_cmd = Command("a", noop, security="owner")
        sudo_cmd = Command("b", noop, security="sudo")

        assert await manager.command_allowed(outgoing_event, owner_cmd, owner_id=42) is True
        assert await manager.command_allowed(incoming_pm, sudo_cmd, owner_id=42) is True


if __name__ == "__main__":
    asyncio.run(main())
