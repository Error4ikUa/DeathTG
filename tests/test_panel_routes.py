from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "deathtg" / "panel"
TEMPLATES = PANEL / "templates"
ROUTE_SOURCES = (PANEL / "clean_app.py", PANEL / "clean_actions.py")


def declared_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for path in ROUTE_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                method = decorator.func.attr.upper()
                if method not in {"GET", "POST"} or not decorator.args:
                    continue
                route = decorator.args[0]
                if isinstance(route, ast.Constant) and isinstance(route.value, str):
                    routes.add((method, route.value))
    return routes


def normalize_template_action(action: str) -> str:
    replacements = {
        r"{{\s*pending_warning\.token\s*}}": "{token}",
        r"{{\s*device\.session_id\s*}}": "{session_id}",
        r"{{\s*module\.name\s*}}": "{name}",
        r"{{\s*module\s*}}": "{name}",
    }
    result = action
    for pattern, value in replacements.items():
        result = re.sub(pattern, value, result)
    return result


class PanelRouteContractTests(unittest.TestCase):
    def test_live_routes_are_unique(self) -> None:
        routes: list[tuple[str, str]] = []
        for path in ROUTE_SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                        continue
                    method = decorator.func.attr.upper()
                    if method in {"GET", "POST"} and decorator.args and isinstance(decorator.args[0], ast.Constant):
                        routes.append((method, str(decorator.args[0].value)))
        self.assertEqual(len(routes), len(set(routes)))

    def test_every_live_post_form_has_a_registered_handler(self) -> None:
        routes = declared_routes()
        live_templates = list(TEMPLATES.glob("clean_*.html")) + [TEMPLATES / "setup.html"]
        missing: list[str] = []
        for template in live_templates:
            text = template.read_text(encoding="utf-8")
            for action in re.findall(r'<form\b[^>]*\bmethod="post"[^>]*\baction="([^"]+)"', text, flags=re.I):
                normalized = normalize_template_action(action)
                if ("POST", normalized) not in routes:
                    missing.append(f"{template.name}: {action} -> {normalized}")
        self.assertFalse(missing, "Missing POST handlers:\n" + "\n".join(missing))

    def test_tabs_and_modals_target_existing_elements(self) -> None:
        errors: list[str] = []
        for template in TEMPLATES.glob("clean_*.html"):
            text = template.read_text(encoding="utf-8")
            ids = set(re.findall(r'\bid="([^"]+)"', text))
            for target in re.findall(r'\bdata-(?:tab|modal-open)="([^"]+)"', text):
                if target not in ids:
                    errors.append(f"{template.name}: missing #{target}")
        self.assertFalse(errors, "Broken UI targets:\n" + "\n".join(errors))

    def test_module_cards_have_a_local_image_fallback(self) -> None:
        for name in ("clean_browser.html", "clean_installed.html"):
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertIn('data-fallback="/images/modules/Module.png"', text, name)
        self.assertTrue((ROOT / "images" / "modules" / "Module.png").is_file())

    def test_legacy_route_layers_are_removed(self) -> None:
        for name in ("pages.py", "state_pages.py", "installmod_route.py", "re_auth.py"):
            self.assertFalse((PANEL / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
