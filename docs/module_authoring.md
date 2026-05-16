# DeathTG Module Author Guide

DeathTG modules should use the framework API only. Modules must not read `BOT_TOKEN`, create a bot client, or register their own callback router.

## Basic Module

```python
from deathtg.loader import Module
from deathtg.command import command


class ExampleMod(Module):
    @command("hello", description="Say hello", usage=".hello")
    async def hello_cmd(self, event, args):
        await event.edit("Hello from DeathTG")
```

## Inline Buttons

Use `self.inline_send` and `self.inline_buttons`. The inline UI is inserted into the chat where the command was called.

```python
await self.inline_send(
    event,
    "<b>Choose an item</b>",
    reply_markup=self.inline_buttons(
        [{"text": "Select", "callback": self.select_callback, "args": ("item-id",)}],
        [{"text": "Close", "callback": self.close_callback, "args": ()}],
    ),
    parse_mode="html",
    link_preview=False,
    ttl=3600,
)

async def select_callback(self, call, item_id):
    await call.edit(f"Selected: <code>{item_id}</code>", reply_markup=None, parse_mode="html")

async def close_callback(self, call):
    await call.edit("Closed.", reply_markup=None)
```

## Sending Files From Callback

Callback updates can come through the inline bot. Use the original chat context saved by DeathTG, not `call.chat_id`.

```python
async def select_callback(self, call, file_path):
    await call.original_client.send_file(call.original_chat_id, file_path, caption="")
    await call.edit("Done.", reply_markup=None)
```

## Module Storage

Each module has a local namespace:

```python
count = self.get("count", 0)
self.set("count", count + 1)
```

For typed config:

```python
from deathtg.loader import ConfigValue, ModuleConfig, validators


class ExampleMod(Module):
    config = ModuleConfig(
        ConfigValue("enabled", True, "Enable module", validators.Boolean()),
        ConfigValue("limit", 5, "Result limit", validators.Integer(minimum=1, maximum=20)),
    )

    async def set_limit_cmd(self, event, args):
        self.config["limit"] = args[0]
        self.save_config()
        await event.edit("Saved.")
```

## Lifecycle Decorators

DeathTG exposes Hikka-inspired decorators without copying Hikka internals:

```python
from deathtg.loader import Module, watcher


class WatchMod(Module):
    @watcher("out", "no_commands")
    async def watch_outgoing(self, event):
        ...
```

Available decorators: `watcher`, `raw_handler`, `inline_handler`, `callback_handler`.

## Command Security

`@command(...)` supports `security=` (or alias `permissions=`). If not set, DeathTG defaults to owner-only access.

```python
@command("public_ping", description="Public ping", usage=".public_ping", security="everyone")
async def public_ping_cmd(self, event, args):
    await event.reply("pong")
```

Supported scopes:

- `owner`
- `sudo` (trusted operator ids)
- `group_admin`
- `group_member`
- `pm`
- `everyone`

You can combine scopes: `security="owner|sudo"` or `security="pm,group_admin"`.

## Forbidden

- Do not read `BOT_TOKEN`.
- Do not create your own bot client.
- Do not call `bot.send_message`.
- Do not use `Button.inline` or `Button.url` directly.
- Do not call `event.client.add_event_handler` from modules.
- Do not send inline UI to the bot private chat.

## Module Metadata For The Web Panel

DeathTG reads lightweight metadata from comments near the top of a module. Use this so the Module Browser can render a rich card before installation.

```python
# meta developer: @your_username
# meta pic: https://example.com/icon.png
# meta banner: https://example.com/banner.png
# scope: inline
# scope: deathtg_min 0.1.0
# requires: aiohttp pillow
```

Recommended repository layout:

```text
YourModule/
  YourModule.py
  Image.png
```

If the module is a single root file, place `Image.png` next to it in the same GitHub folder. The panel checks `Image.png`, `image.png`, `<module>.png`, `<module>.jpg`, and `<module>.webp`.

Panel fields:

- `# meta developer:` is shown as Author.
- `# meta pic:` can be used as icon metadata later.
- `# meta banner:` can be used as banner metadata later.
- `# scope: inline` marks modules that depend on inline buttons.
- `# requires:` lists pip dependencies for the module passport.
- `Image.png` becomes the module card background.
- `@command(... description=..., usage=...)` becomes the command list in module info.

Keep module code on the DeathTG contract:

```python
from deathtg.loader import Module
from deathtg.command import command

class ExampleModule(Module):
    strings = {"name": "Example"}

    @command("example", description="Show example panel", usage=".example")
    async def example_cmd(self, event, args):
        await self.inline_send(
            event,
            "<b>Example</b>",
            reply_markup=self.inline_buttons(
                [{"text": "Close", "callback": self.close_callback, "args": ()}],
            ),
            parse_mode="html",
            link_preview=False,
            ttl=3600,
        )

    async def close_callback(self, call):
        await call.edit("Closed.", reply_markup=None)
```

Do not read `BOT_TOKEN`, create bot clients, send UI to bot private chat, or use `Button.inline` directly. Use only `self.inline_send`, `self.inline_buttons`, `self.inline_form`, `self.inline_list`, or `self.inline_gallery`.

## Parity Features Now Supported

The web panel can now show and edit `ModuleConfig` values from installed modules. Use typed config values so users can configure the module without editing code:

```python
from deathtg.loader import Module, ModuleConfig, ConfigValue, validators

class ExampleModule(Module):
    strings = {"name": "Example"}
    config = ModuleConfig(
        ConfigValue("enabled", True, "Enable module features", validators.Boolean()),
        ConfigValue("mode", "safe", "Working mode", validators.Choice(["safe", "fast"])),
        ConfigValue("api_key", "", "Optional service API key", validators.String(), secret=True),
    )
```

Config values appear in the module passport at `/modules/<module>`. Secret values are masked in UI and blank secret fields keep the previous value.

The panel also displays scanner verdicts, score, findings, handler counts, scopes and requirements. For installed modules with safe `# requires:` entries, the panel can install dependencies from the module detail page.

For quick copy-paste AI instructions, see `docs/module_prompt_for_devs.md`.
