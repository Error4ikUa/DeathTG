from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

import aiohttp

from deathtg.command import Command
from deathtg.registry import CommandRegistry
from deathtg.security import scan_module_source


class ModuleLoader:
    def __init__(self, registry: CommandRegistry, modules_dir: Path) -> None:
        self.registry = registry
        self.modules_dir = modules_dir
        self.loaded: dict[str, ModuleType] = {}
        self.modules_dir.mkdir(parents=True, exist_ok=True)

    async def load_builtin(self, package: str, module_names: list[str]) -> None:
        for name in module_names:
            module = importlib.import_module(f"{package}.{name}")
            self._register_module(module, name)

    async def load_all_local(self) -> None:
        for path in sorted(self.modules_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                await self.load_file(path)
            except Exception as exc:
                print(f"[DeathTG] skip module {path.name}: {exc}")

    async def load_file(self, path: Path, *, force: bool = False) -> str:
        if not path.exists() or path.suffix != ".py":
            raise FileNotFoundError("Нужен существующий .py файл модуля")

        source = path.read_text(encoding="utf-8")
        report = scan_module_source(source)
        if not report.allowed and not force:
            raise RuntimeError("Модуль заблокирован защитой:\n" + report.pretty())

        module_name = path.stem
        import_name = f"deathtg_user_modules.{module_name}"
        self.unload(module_name, silent=True, force=True)

        spec = importlib.util.spec_from_file_location(import_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Не могу прочитать модуль: {path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[import_name] = module
        spec.loader.exec_module(module)
        self._register_module(module, module_name)
        return module_name

    async def download_module(self, link: str) -> Path:
        url = self._normalize_github_url(link.strip())
        filename = Path(urlparse(url).path).name or "module.py"
        if not filename.endswith(".py"):
            raise RuntimeError("Ссылка должна вести на .py модуль")

        target = self.modules_dir / filename
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as response:
                if response.status != 200:
                    raise RuntimeError(f"Не скачалось, HTTP {response.status}")
                text = await response.text()

        if "from deathtg.command import command" not in text and "@command" not in text:
            raise RuntimeError("Это не похоже на DeathTG-модуль")

        report = scan_module_source(text)
        if not report.allowed:
            raise RuntimeError("Модуль заблокирован защитой:\n" + report.pretty())

        target.write_text(text, encoding="utf-8")
        return target

    def unload(self, module_name: str, *, silent: bool = False, force: bool = False) -> list[str]:
        removed = self.registry.remove_module(module_name, force=force)
        self.loaded.pop(module_name, None)
        for key in list(sys.modules):
            if key.endswith(f".{module_name}"):
                sys.modules.pop(key, None)
        if not removed and not silent:
            raise RuntimeError(f"Модуль не найден: {module_name}")
        return removed

    def _register_module(self, module: ModuleType, module_name: str) -> None:
        registered = 0
        for _, obj in inspect.getmembers(module, inspect.iscoroutinefunction):
            meta = getattr(obj, "__deathtg_command__", None)
            if not meta:
                continue
            self.registry.add(
                Command(
                    name=meta["name"],
                    handler=obj,
                    description=meta["description"],
                    usage=meta["usage"],
                    aliases=meta["aliases"],
                    module=module_name,
                )
            )
            registered += 1

        if registered == 0:
            raise RuntimeError(f"В модуле {module_name} нет команд")

        self.loaded[module_name] = module

    @staticmethod
    def _normalize_github_url(link: str) -> str:
        if "github.com" in link and "/blob/" in link:
            return link.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
        return link
