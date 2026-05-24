from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from deathtg.server_bootstrap import ensure_server_env, parse_env_file, secure_panel_secret, update_env_values


def main() -> None:
    assert secure_panel_secret("change_me_to_random_long_string") != "change_me_to_random_long_string"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ".env"
        update_env_values({"PANEL_SECRET": "change_me_to_random_long_string"}, path=path)
        env = ensure_server_env(path=path, panel_host="127.0.0.1", panel_port="8080")
        assert env["PANEL_HOST"] == "127.0.0.1"
        assert env["PANEL_PORT"] == "8080"
        assert env["PANEL_SECRET"] != "change_me_to_random_long_string"
        assert "BOT_TOKEN_COMMUNITY" in env
        assert "COMMUNITY_BOT_USERNAME" in env
        parsed = parse_env_file(path)
        assert parsed["PANEL_SECRET"] == env["PANEL_SECRET"]

    print("smoke_server_bootstrap: ok")


if __name__ == "__main__":
    main()
