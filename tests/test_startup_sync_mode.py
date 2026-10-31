from __future__ import annotations

import unittest
from unittest.mock import patch

import deathtg.startup_sync as startup_sync


class StartupSyncModeTests(unittest.TestCase):
    def test_telegram_only_does_not_issue_or_send_a_panel_grant(self) -> None:
        values = {"DEATHTG_MODE_SELECTED": "1", "DEATHTG_MODE": "telegram"}
        with (
            patch.object(startup_sync, "_env", side_effect=lambda key: values.get(key, "")),
            patch.object(startup_sync, "_build_panel_grant_url") as issue_grant,
        ):
            panel_url = startup_sync._panel_grant_url_for_mode(123)
            buttons = startup_sync._owner_shortcut_buttons(panel_url, "", "", "")

        self.assertEqual(panel_url, "")
        self.assertEqual(buttons, [])
        issue_grant.assert_not_called()

    def test_web_mode_keeps_the_panel_grant_button(self) -> None:
        values = {"DEATHTG_MODE_SELECTED": "1", "DEATHTG_MODE": "web"}
        with (
            patch.object(startup_sync, "_env", side_effect=lambda key: values.get(key, "")),
            patch.object(startup_sync, "_build_panel_grant_url", return_value="https://private.example/grant"),
        ):
            panel_url = startup_sync._panel_grant_url_for_mode(123)
            buttons = startup_sync._owner_shortcut_buttons(panel_url, "", "", "")

        self.assertEqual(buttons, [[{"text": "Open DeathTG", "url": "https://private.example/grant"}]])


if __name__ == "__main__":
    unittest.main()
