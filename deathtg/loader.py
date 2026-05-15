from __future__ import annotations

"""
Module loading and registration for DeathTG.

The ``ModuleLoader`` class in this file is responsible for
downloading, importing and registering modules.  It handles both
built‑in modules (shipped with DeathTG) and third‑party modules
downloaded from arbitrary URLs.  Loading errors are caught and
reported to the console so that the control panel continues to
function even when individual modules fail to import.

Some key points:

* Modules are loaded into the ``deathtg.modules_external`` namespace
  to isolate them from built‑ins.
* Commands are registered via the ``CommandRegistry`` and decorated
  with the ``@command`` decorator.
* The loader provides convenience methods to download modules from
  GitHub raw or blob URLs, automatically normalising URLs and
  validating that the downloaded content is Python code rather than
  HTML.
* When a module is unloaded all of its commands are removed from the
  registry and any traces from ``sys.modules`` are cleared to allow a
  fresh reload.
"""

import importlib
import importlib.util
import inspect
import sys
import traceback
import types
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

import aiohttp

from deathtg.command import Command, command
from deathtg.registry import CommandRegistry
from deathtg.security import scan_module_source


class Module:
    """Base class for module classes that expose coroutine methods."""

    strings: dict = {}

    def __init__(self) -> None:
        self.client = None
        self.app = None


def owner(func=None, *args, **kwargs):
    """Compatibility decorator; noop in this loader."""

    def deco(f):
        return f
    return deco(func) if callable(func) else deco


def unrestricted(func=None, *args, **kwargs):
    """Compatibility decorator; noop in this loader."""

    def deco(f):
        return f
    return deco(func) if callable(func) else deco


class ModuleLoader:
    """Load, register and unload modules for DeathTG."""

    def __init__(self, registry: CommandRegistry, modules_dir: Path) -> None:
        self.registry = registry
        self.modules_dir = modules_dir
        self.loaded: dict[str, ModuleType] = {}
        self.modules_dir.mkdir(parents=True, exist_ok=True)

    async def load_builtin(self, package: str, module_names: list[str]) -> None:
        for name in module_names:
            try:
                import_name = f"{package}.{name}"
                # If the module was already loaded, reload it to update code
                if import_name in sys.modules:
                    module = importlib.reload(sys.modules[import_name])
                else:
                    module = importlib.import_module(import_name)
                self._register_module(module, name)
            except Exception as exc:
                # Catch exceptions to prevent a single bad module from stopping the panel
                print(f"\n[DeathTG] ⚠️ КРИТИЧЕСКАЯ ОШИБКА В СИСТЕМНОМ МОДУЛЕ '{name}':")
                traceback.print_exc()
                print("[DeathTG] Панель продолжит работу, но этот модуль не загружен.\n")

    async def load_all_local(self) -> None:
        for path in sorted(self.modules_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                await self.load_file(path)
            except Exception as exc:
                print(f"[DeathTG] skip module {path.name}: {exc}")

    def _install_compat_aliases(self) -> None:
        core_pkg = importlib.import_module("deathtg")
        sys.modules.setdefault("DeathTG", core_pkg)
        for sub in ("command", "config", "registry", "security", "ui", "loader"):
            try:
                mod = importlib.import_module(f"deathtg.{sub}")
                sys.modules.setdefault(f"DeathTG.{sub}", mod)
                setattr(core_pkg, sub, mod)
            except Exception:
                pass
        if "DeathTG.utils" not in sys.modules:
            utils = types.ModuleType("DeathTG.utils")
            async def answer(event, text=None, **kwargs):
                if text is None:
                    text = ""
                if hasattr(event, "edit"):
                    try:
                        return await event.edit(text, **kwargs)
                    except Exception:
                        pass
                if hasattr(event, "reply"):
                    return await event.reply(text, **kwargs)
                return None
            utils.answer = answer
            utils.reply = answer
            sys.modules["DeathTG.utils"] = utils
            setattr(core_pkg, "utils", utils)

    async def load_file(self, path: Path, *, force: bool = False) -> str:
        if not path.exists() or path.suffix != ".py":
            raise FileNotFoundError("Нужен существующий .py файл модуля")
        source = path.read_text(encoding="utf-8")
        report = scan_module_source(source)
        if not report.allowed and not force:
            raise RuntimeError("Модуль заблокирован защитой:\n" + report.pretty())
        self._install_compat_aliases()
        module_name = path.stem
        import_name = f"deathtg.modules_external.{module_name}"
        self.unload(module_name, silent=True, force=True)
        spec = importlib.util.spec_from_file_location(import_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Не могу прочитать модуль: {path}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "deathtg.modules_external"
        sys.modules[import_name] = module
        spec.loader.exec_module(module)
        self._register_module(module, module_name)
        return module_name

    async def download_module(self, link: str) -> Path:
        url = self._normalize_github_url(link)
        filename = Path(urlparse(url).path).name or "module.py"
        if not filename.endswith(".py"):
            raise RuntimeError("Ссылка должна вести на .py модуль")
        target = self.modules_dir / filename
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Не скачалось, HTTP {response.status}")
                    text = await response.text()
        except aiohttp.InvalidURL as exc:
            raise RuntimeError("Некорректная ссылка на модуль") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Ошибка скачивания модуля: {exc}") from exc
        if self._looks_like_html(text):
            raise RuntimeError("По ссылке пришла HTML-страница, а не .py код. Дай raw/blob ссылку")
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

    def _wrap_handler(self, obj):
        async def wrapped(event, args):
            try:
                if len(inspect.signature(obj).parameters) <= 1:
                    return await obj(event)
            except Exception:
                pass
            return await obj(event, args)
        return wrapped

    def _add_command(self, obj, module_name: str) -> bool:
        meta = getattr(obj, "__deathtg_command__", None)
        if not meta:
            return False
        self.registry.add(
            Command(
                name=meta["name"],
                handler=self._wrap_handler(obj),
                description=meta["description"],
                usage=meta["usage"],
                aliases=meta["aliases"],
                module=module_name,
            )
        )
        return True

    def _register_module(self, module: ModuleType, module_name: str) -> None:
        registered = 0
        for _, obj in inspect.getmembers(module, inspect.iscoroutinefunction):
            if self._add_command(obj, module_name):
                registered += 1
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is Module:
                continue
            if not issubclass(cls, Module):
                continue
            inst = cls()
            for attr_name in dir(inst):
                if attr_name.startswith("_"):
                    continue
                obj = getattr(inst, attr_name)
                if not (inspect.iscoroutinefunction(obj) or inspect.ismethod(obj)):
                    continue
                if not getattr(obj, "__deathtg_command__", None) and attr_name.endswith("cmd"):
                    obj = command(
                        attr_name[:-3].lower(),
                        description=f"{attr_name[:-3]} command",
                        usage=f".{attr_name[:-3].lower()}",
                    )(obj)
                if self._add_command(obj, module_name):
                    registered += 1
        if registered == 0:
            raise RuntimeError(f"В модуле {module_name} нет команд (декоратор @command не найден)")
        self.loaded[module_name] = module

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        head = text[:500].lower()
        return "<!doctype html" in head or "<html" in head or "<body" in head

    @staticmethod
    def _normalize_github_url(link: str) -> str:
        url = (link or "").strip().strip("'\"")
        if not url:
            raise RuntimeError("Вставь ссылку на .py модуль")
        if url.startswith("www."):
            url = "https://" + url
        if url.startswith("github.com/"):
            url = "https://" + url
        if "github.com" in url and "/blob/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("Ссылка должна быть полной: https://.../module.py")
        return url