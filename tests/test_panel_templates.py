from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "deathtg" / "panel" / "templates"
STATIC_DIR = ROOT / "deathtg" / "panel" / "static"


class PanelTemplateTests(unittest.TestCase):
    def test_control_templates_do_not_embed_style_or_event_handlers(self) -> None:
        offenders: list[str] = []
        for path in sorted(TEMPLATE_DIR.glob("*.html")):
            source = path.read_text(encoding="utf-8")
            if "<style" in source.lower():
                offenders.append(f"{path.name}: embedded style block")
            if re.search(r"\sstyle\s*=", source, flags=re.IGNORECASE):
                offenders.append(f"{path.name}: inline style")
            if re.search(r"\son[a-z]+\s*=", source, flags=re.IGNORECASE):
                offenders.append(f"{path.name}: inline event handler")
        self.assertEqual(offenders, [])

    def test_navigation_controls_expose_accessibility_state(self) -> None:
        for path in sorted(TEMPLATE_DIR.glob("clean_*.html")):
            source = path.read_text(encoding="utf-8")
            if 'id="menuToggle"' not in source:
                continue
            with self.subTest(template=path.name):
                self.assertIn('aria-controls="drawer"', source)
                self.assertIn('aria-expanded="false"', source)

    def test_mobile_profile_editor_uses_non_overlapping_grid_rows(self) -> None:
        source = (STATIC_DIR / "engineering.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-rows:max-content max-content !important", source)
        self.assertIn(".editor-form { overflow:visible !important; }", source)

    def test_setup_completion_explains_each_mode_without_public_url_advice(self) -> None:
        source = (TEMPLATE_DIR / "setup_done.html").read_text(encoding="utf-8")
        self.assertIn("Continue using DeathTG in Telegram", source)
        self.assertIn("Продолжай управлять DeathTG через Telegram", source)
        self.assertIn("same Tailscale account", source)
        self.assertIn("тот же аккаунт Tailscale", source)
        self.assertNotIn("LAN/public", source)

    def test_setup_mode_back_link_matches_selected_language(self) -> None:
        source = (TEMPLATE_DIR / "setup.html").read_text(encoding="utf-8")
        self.assertIn("Back to mode selection", source)
        self.assertIn("К выбору режима", source)
        self.assertEqual(source.count('href="/setup?step=mode"'), 3)

    def test_module_catalog_tabs_and_details_keep_navigation_context(self) -> None:
        browser = (TEMPLATE_DIR / "clean_browser.html").read_text(encoding="utf-8")
        details = (TEMPLATE_DIR / "clean_module_detail.html").read_text(encoding="utf-8")
        self.assertIn('role="tablist"', browser)
        self.assertIn('aria-controls="installedPane"', browser)
        self.assertIn("return_page", browser)
        self.assertIn("return_to=installed_tab&amp;page={{ browser_page }}", browser)
        self.assertIn("{{ module_back_url }}", details)
        self.assertIn("module-detail.css?v=1", details)


if __name__ == "__main__":
    unittest.main()
