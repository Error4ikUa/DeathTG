from __future__ import annotations

import unittest

from deathtg.panel.clean_actions import _module_detail_path, _query_target, _target_path


class PanelNavigationTests(unittest.TestCase):
    def test_return_target_keeps_browser_tab_and_page(self) -> None:
        self.assertEqual(
            _target_path("installed_tab", return_page="3"),
            "/browser?page=3#installedPane",
        )
        self.assertEqual(
            _target_path("install_tab", return_page="2"),
            "/browser?page=2#installPane",
        )
        self.assertEqual(
            _target_path("browser", return_page="0"),
            "/browser",
        )

    def test_query_parameters_are_added_before_tab_fragment(self) -> None:
        self.assertEqual(
            _query_target("/browser?page=2#installPane", "warning", "safe_token"),
            "/browser?page=2&warning=safe_token#installPane",
        )

    def test_module_detail_return_context_is_whitelisted(self) -> None:
        self.assertEqual(
            _module_detail_path("Sample Module", "installed_tab", "4"),
            "/modules/Sample%20Module?return_to=installed_tab&page=4",
        )
        self.assertEqual(
            _module_detail_path("Sample", "https://attacker.test", "-3"),
            "/modules/Sample?return_to=installed_tab&page=1",
        )


if __name__ == "__main__":
    unittest.main()
