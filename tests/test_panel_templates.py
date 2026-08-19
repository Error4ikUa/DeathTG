from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "deathtg" / "panel" / "templates"


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


if __name__ == "__main__":
    unittest.main()
