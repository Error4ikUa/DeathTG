from __future__ import annotations

"""
Module loading and registration for DeathTG.

The ``ModuleLoader`` class in this file is responsible for
downloading, importing and registering modules.  It handles both
built-in modules (shipped with DeathTG) and third-party modules
downloaded from arbitrary URLs.  Loading errors are caught and
reported to the console so that the control panel continues to
function even when individual modules fail to import.

Some key points:

* Modules are loaded into the ``deathtg.modules_external`` namespace
  to isolate them from built-ins.
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

import asyncio
import importlib
import importlib.util
import inspect
import secrets
import sys
import time
import traceback
import types
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

import aiohttp
from deathtg.command import Command, command
from deathtg.module_config import ConfigValue, ModuleConfig, ValidationError, validators
from deathtg.module_db import ModuleDatabase
from deathtg.registry import CommandRegistry
from deathtg.security import scan_module_source


def _mark_handler(kind: str, *tags, **filters):
    def decorator(func):
        setattr(
            func,
            f"__deathtg_{kind}__",
            {
                "tags": tuple(str(tag) for tag in tags),
                "filters": dict(filters),
            },
        )
        return func

    return decorator


def watcher(*tags, **filters):
    """Mark a coroutine as a DeathTG message watcher."""

    return _mark_handler("watcher", *tags, **filters)


def callback_handler(*tags, **filters):
    """Mark a coroutine as a named callback handler for future inline routing."""

    return _mark_handler("callback_handler", *tags, **filters)


def inline_handler(*tags, **filters):
    """Mark a coroutine as an inline query handler for future inline routing."""

    return _mark_handler("inline_handler", *tags, **filters)


def raw_handler(*updates):
    """Mark a coroutine as a raw Telethon update handler."""

    def decorator(func):
        setattr(
            func,
            "__deathtg_raw_handler__",
            {
                "updates": tuple(updates),
                "tags": (),
                "filters": {},
            },
        )
        return func

    return decorator


class Module:
    """Base class for module classes that expose coroutine methods."""

    strings: dict = {}

    def __init__(self) -> None:
        self.client = None
        self.app = None
        self.inline = None
        self.cache: dict[str, dict] = {}
        self._module_name = self.__class__.__name__
        self._db: ModuleDatabase | None = None

    async def inline_template(
        self,
        text: str,
        *,
        message=None,
        open_url: str | None = None,
        next_callback=None,
        next_args: tuple | list | None = None,
        ttl: int = 3600,
        parse_mode: str | None = None,
    ):
        """Unified inline bridge for DTG modules bound to the current owner's inline bot."""
        if not self.inline:
            if message is not None and hasattr(message, "edit"):
                return await message.edit("Inline bot is not configured")
            return None
        markup = []
        row = []
        if open_url:
            row.append({"text": "Open", "url": open_url})
        if next_callback:
            row.append({"text": "Next", "callback": next_callback, "args": tuple(next_args or ())})
        if row:
            markup.append(row)
        return await self.inline.form(
            text,
            message=message,
            reply_markup=markup or None,
            ttl=ttl,
            parse_mode=parse_mode,
        )

    async def inline_send(
        self,
        event,
        text: str,
        *,
        buttons=None,
        reply_markup=None,
        parse_mode: str | None = "html",
        link_preview: bool | None = False,
        ttl: int = 3600,
    ):
        """Unified inline sending API for all modules (with safe fallback)."""
        markup = reply_markup if reply_markup is not None else buttons
        if self.inline:
            return await self.inline.send_or_edit(
                event,
                text,
                reply_markup=markup,
                parse_mode=parse_mode,
                link_preview=link_preview,
                ttl=ttl,
            )
        if event is not None and hasattr(event, "edit"):
            return await event.edit(text, buttons=markup, parse_mode=parse_mode, link_preview=link_preview)
        return None

    async def inline_form(self, event, text: str, **kwargs):
        """Hikka-like alias for the safe DeathTG inline sender."""

        return await self.inline_send(event, text, **kwargs)

    async def inline_list(
        self,
        event,
        title: str,
        items: list[dict] | list[str],
        *,
        page: int = 0,
        per_page: int = 8,
        close_callback=None,
        ttl: int = 3600,
    ):
        total = len(items)
        page = max(0, page)
        start = page * per_page
        chunk = items[start:start + per_page]
        lines = [title, "", f"Page {page + 1} / {max(1, (total + per_page - 1) // per_page)}"]
        rows = []
        for idx, item in enumerate(chunk, start=start):
            if isinstance(item, dict):
                lines.append(str(item.get("text") or item.get("title") or item))
                if item.get("callback"):
                    rows.append([{
                        "text": str(item.get("button") or item.get("text") or f"Item {idx + 1}")[:32],
                        "callback": item["callback"],
                        "args": tuple(item.get("args") or (idx,)),
                    }])
            else:
                lines.append(str(item))
        nav = []
        if page > 0:
            nav.append({"text": "Back", "callback": self._inline_list_page, "args": (title, items, page - 1, per_page)})
        if start + per_page < total:
            nav.append({"text": "Next", "callback": self._inline_list_page, "args": (title, items, page + 1, per_page)})
        if nav:
            rows.append(nav)
        if close_callback:
            rows.append([{"text": "Close", "callback": close_callback, "args": ()}])
        return await self.inline_send(
            event,
            "\n".join(lines),
            reply_markup=self.inline_buttons(*rows),
            ttl=ttl,
        )

    async def _inline_list_page(self, call, title: str, items: list, page: int, per_page: int):
        total = len(items)
        start = page * per_page
        chunk = items[start:start + per_page]
        lines = [title, "", f"Page {page + 1} / {max(1, (total + per_page - 1) // per_page)}"]
        for item in chunk:
            lines.append(str(item.get("text") if isinstance(item, dict) else item))
        rows = []
        nav = []
        if page > 0:
            nav.append({"text": "Back", "callback": self._inline_list_page, "args": (title, items, page - 1, per_page)})
        if start + per_page < total:
            nav.append({"text": "Next", "callback": self._inline_list_page, "args": (title, items, page + 1, per_page)})
        if nav:
            rows.append(nav)
        await call.edit("\n".join(lines), reply_markup=self.inline_buttons(*rows))

    async def inline_gallery(self, event, title: str, cards: list[str], *, page: int = 0, ttl: int = 3600):
        """Minimal text-card gallery. Rich media can be layered on top later."""

        if not cards:
            return await self.inline_send(event, title, ttl=ttl)
        page = max(0, min(page, len(cards) - 1))
        return await self.inline_send(
            event,
            f"{title}\n\n{cards[page]}\n\n{page + 1}/{len(cards)}",
            reply_markup=self.inline_buttons(
                [
                    {"text": "Back", "callback": self._inline_gallery_page, "args": (title, cards, max(0, page - 1))},
                    {"text": "Next", "callback": self._inline_gallery_page, "args": (title, cards, min(len(cards) - 1, page + 1))},
                ],
            ),
            ttl=ttl,
        )

    async def _inline_gallery_page(self, call, title: str, cards: list[str], page: int):
        page = max(0, min(page, len(cards) - 1))
        await call.edit(
            f"{title}\n\n{cards[page]}\n\n{page + 1}/{len(cards)}",
            reply_markup=self.inline_buttons(
                [
                    {"text": "Back", "callback": self._inline_gallery_page, "args": (title, cards, max(0, page - 1))},
                    {"text": "Next", "callback": self._inline_gallery_page, "args": (title, cards, min(len(cards) - 1, page + 1))},
                ],
            ),
        )

    @staticmethod
    def inline_buttons(*rows):
        """
        Helper to build reply_markup rows for inline manager.
        Rows are tuples/lists of button dicts:
        {"text": "...", "url": "..."} or
        {"text": "...", "callback": self.some_handler, "args": (...)}
        """
        out = []
        for row in rows:
            if not row:
                continue
            out.append(list(row))
        return out or None

    def cache_event(self, event, data=None, *, ttl: int = 3600) -> str:
        """
        Cache source context for callbacks that must send result to the original chat.
        Returns cache key.
        """
        key = f"dtg:{int(time.time())}:{secrets.token_hex(8)}"
        self.cache[key] = {
            "chat_id": getattr(event, "chat_id", None),
            "client": getattr(event, "client", None) or self.client,
            "data": data,
            "time": time.time(),
            "ttl": int(ttl or 3600),
        }
        self._cleanup_cache()
        return key

    def get_cached(self, key: str):
        item = self.cache.get(key)
        if not item:
            return None
        age = time.time() - float(item.get("time", 0))
        ttl = int(item.get("ttl", 3600) or 3600)
        if age > ttl:
            self.cache.pop(key, None)
            return None
        return item

    def _cleanup_cache(self) -> None:
        now = time.time()
        for key, item in list(self.cache.items()):
            ttl = int(item.get("ttl", 3600) or 3600)
            if now - float(item.get("time", 0)) > ttl:
                self.cache.pop(key, None)

    def get(self, key: str, default=None):
        if not self._db:
            self._db = ModuleDatabase()
        return self._db.get(self._module_name, key, default)

    def set(self, key: str, value):
        if not self._db:
            self._db = ModuleDatabase()
        return self._db.set(self._module_name, key, value)

    def save_config(self) -> None:
        cfg = getattr(self, "config", None)
        if isinstance(cfg, ModuleConfig):
            if not self._db:
                self._db = ModuleDatabase()
            self._db.set(self._module_name, "config", cfg.dump(include_secrets=True))


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
        self.instances: dict[str, list[Module]] = {}
        self.watchers: dict[str, list] = {}
        self.raw_handlers: dict[str, list] = {}
        self.inline_handlers: dict[str, list] = {}
        self.callback_handlers: dict[str, list] = {}
        self.storage = ModuleDatabase()
        self.app = None
        self.client = None
        self.inline_manager = None
        self.modules_dir.mkdir(parents=True, exist_ok=True)

    def bind(self, *, app=None, client=None, inline_manager=None) -> None:
        self.app = app
        self.client = client
        if inline_manager is not None:
            self.inline_manager = inline_manager
        for instances in self.instances.values():
            for inst in instances:
                inst.app = self.app
                inst.client = self.client
                inst.inline = self.inline_manager
                inst._db = self.storage

    async def load_builtin(self, package: str, module_names: list[str]) -> None:
        for name in module_names:
            try:
                import_name = f"{package}.{name}"
                # If the module was already loaded, reload it to update code
                if import_name in sys.modules:
                    module = importlib.reload(sys.modules[import_name])
                else:
                    module = importlib.import_module(import_name)
                self.registry.remove_module(name, force=True)
                await self._register_module(module, name)
            except Exception as exc:
                self.registry.remove_module(name, force=True)
                self.loaded.pop(name, None)
                # Catch exceptions to prevent a single bad module from stopping the panel
                print(f"\n[DeathTG] CRITICAL ERROR in built-in module '{name}':")
                traceback.print_exc()
                print("[DeathTG] Continuing startup without this module.\n")

    async def load_all_local(self, *, force_modules: set[str] | None = None) -> None:
        force_modules = force_modules or set()
        for path in sorted(self.modules_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                await self.load_file(path, force=path.stem in force_modules)
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
            sys.modules.setdefault("deathtg.utils", utils)
            setattr(core_pkg, "utils", utils)

    async def load_file(self, path: Path, *, force: bool = False) -> str:
        if not path.exists() or path.suffix != ".py":
            raise FileNotFoundError("Expected an existing .py module file")
        source = path.read_text(encoding="utf-8")
        report = scan_module_source(source, trusted=force)
        if not report.allowed and not force:
            raise RuntimeError("Module was blocked by security scan:\n" + report.pretty())
        self._install_compat_aliases()
        module_name = path.stem
        import_name = f"deathtg.modules_external.{module_name}"
        self.unload(module_name, silent=True, force=True)
        spec = importlib.util.spec_from_file_location(import_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load module file: {path}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "deathtg.modules_external"
        try:
            sys.modules[import_name] = module
            spec.loader.exec_module(module)
            await self._register_module(module, module_name)
        except Exception:
            self.registry.remove_module(module_name, force=True)
            self.loaded.pop(module_name, None)
            sys.modules.pop(import_name, None)
            raise
        return module_name

    async def download_module(self, link: str, *, force: bool = False) -> Path:
        url = self._normalize_github_url(link)
        filename = Path(urlparse(url).path).name or "module.py"
        if not filename.endswith(".py"):
            raise RuntimeError("URL must point to a .py module")
        target = self.modules_dir / filename
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        raise RuntimeError(f"Download failed, HTTP {response.status}")
                    text = await response.text()
        except aiohttp.InvalidURL as exc:
            raise RuntimeError("Invalid module URL") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Module download error: {exc}") from exc
        if self._looks_like_html(text):
            raise RuntimeError("URL returned HTML instead of Python code. Use a raw/blob module URL.")
        report = scan_module_source(text, trusted=force)
        if not report.allowed and not force:
            raise RuntimeError("Module was blocked by security scan:\n" + report.pretty())
        target.write_text(text, encoding="utf-8")
        return target

    def unload(self, module_name: str, *, silent: bool = False, force: bool = False) -> list[str]:
        removed = self.registry.remove_module(module_name, force=force)
        for inst in self.instances.get(module_name, []):
            self._schedule_hook(inst, "on_unload")
        self.loaded.pop(module_name, None)
        self.instances.pop(module_name, None)
        self._forget_module_handlers(module_name)
        for key in list(sys.modules):
            if key.endswith(f".{module_name}"):
                sys.modules.pop(key, None)
        if not removed and not silent:
            raise RuntimeError(f"Module not found: {module_name}")
        return removed

    def _forget_module_handlers(self, module_name: str) -> None:
        self.watchers.pop(module_name, None)
        self.raw_handlers.pop(module_name, None)
        self.inline_handlers.pop(module_name, None)
        self.callback_handlers.pop(module_name, None)

    def _schedule_hook(self, inst: Module, hook_name: str) -> None:
        hook = getattr(inst, hook_name, None)
        if not hook:
            return

        async def runner():
            await self._call_hook(inst, hook_name)

        try:
            asyncio.get_running_loop().create_task(runner())
        except RuntimeError:
            pass

    async def _call_hook(self, inst: Module, hook_name: str) -> None:
        hook = getattr(inst, hook_name, None)
        if not hook:
            return
        try:
            if hook_name == "client_ready":
                params = inspect.signature(hook).parameters
                if len(params) >= 2:
                    result = hook(self.client, self.storage)
                elif len(params) == 1:
                    result = hook(self.client)
                else:
                    result = hook()
            else:
                result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception:
            traceback.print_exc()

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
        if meta is None and hasattr(obj, "__func__"):
            meta = getattr(obj.__func__, "__deathtg_command__", None)
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
                security=meta.get("security"),
            )
        )
        return True

    async def _register_module(self, module: ModuleType, module_name: str) -> None:
        registered = 0
        self.instances[module_name] = []
        self._forget_module_handlers(module_name)
        for _, obj in inspect.getmembers(module, inspect.iscoroutinefunction):
            if self._add_command(obj, module_name):
                registered += 1
            self._add_lifecycle_handler(obj, module_name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is Module:
                continue
            if not issubclass(cls, Module):
                continue
            inst = cls()
            inst.app = self.app
            inst.client = self.client
            inst.inline = self.inline_manager
            inst._db = self.storage
            inst._module_name = module_name
            self._load_config(inst, module_name)
            self.instances[module_name].append(inst)
            for attr_name in dir(inst):
                if attr_name.startswith("_"):
                    continue
                bound = getattr(inst, attr_name)
                fn = bound.__func__ if inspect.ismethod(bound) else bound
                if not inspect.iscoroutinefunction(fn):
                    continue
                if not getattr(fn, "__deathtg_command__", None) and attr_name.endswith("cmd"):
                    fn = command(
                        attr_name[:-3].lower(),
                        description=f"{attr_name[:-3]} command",
                        usage=f".{attr_name[:-3].lower()}",
                    )(fn)
                    bound = fn.__get__(inst, cls)
                if self._add_command(bound, module_name):
                    registered += 1
                self._add_lifecycle_handler(bound, module_name)
            await self._call_hook(inst, "on_load")
            if self.client is not None:
                await self._call_hook(inst, "client_ready")
        handler_total = (
            len(self.watchers.get(module_name, []))
            + len(self.raw_handlers.get(module_name, []))
            + len(self.inline_handlers.get(module_name, []))
            + len(self.callback_handlers.get(module_name, []))
        )
        if registered == 0 and handler_total == 0:
            raise RuntimeError(f"Module {module_name} has no commands (@command not found)")
        self.loaded[module_name] = module

    def _load_config(self, inst: Module, module_name: str) -> None:
        cfg = getattr(inst, "config", None)
        if isinstance(cfg, ModuleConfig):
            cfg.load(self.storage.get(module_name, "config", {}))
            self.storage.set(module_name, "config", cfg.dump(include_secrets=True))

    def _add_lifecycle_handler(self, obj, module_name: str) -> None:
        for kind, bucket in (
            ("watcher", self.watchers),
            ("raw_handler", self.raw_handlers),
            ("inline_handler", self.inline_handlers),
            ("callback_handler", self.callback_handlers),
        ):
            meta = self._handler_meta(obj, kind)
            if meta is not None:
                bucket.setdefault(module_name, []).append((obj, meta))

    @staticmethod
    def _handler_meta(obj, kind: str):
        attr = f"__deathtg_{kind}__"
        meta = getattr(obj, attr, None)
        if meta is None and hasattr(obj, "__func__"):
            meta = getattr(obj.__func__, attr, None)
        return meta

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        head = text[:500].lower()
        return "<!doctype html" in head or "<html" in head or "<body" in head

    @staticmethod
    def _normalize_github_url(link: str) -> str:
        url = (link or "").strip().strip("'\"")
        if not url:
            raise RuntimeError("Provide a URL to a .py module")
        if url.startswith("www."):
            url = "https://" + url
        if url.startswith("github.com/"):
            url = "https://" + url
        if "github.com" in url and "/blob/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("URL must be absolute: https://.../module.py")
        return url


__all__ = [
    "Module",
    "ModuleLoader",
    "command",
    "watcher",
    "callback_handler",
    "inline_handler",
    "raw_handler",
    "owner",
    "unrestricted",
    "ModuleConfig",
    "ConfigValue",
    "ValidationError",
    "validators",
]
