# DeathTG Module Prompt (For External Devs)

Use this prompt when asking an AI to generate a DeathTG module.

```text
Generate a complete DeathTG module for Telethon.

Hard requirements:
1) Imports:
- from deathtg.loader import Module
- from deathtg.command import command

2) Class:
- class <YourName>(Module)

3) Commands:
- Use only @command(...)
- Handler signature:
  async def xxx_cmd(self, event, args)

4) Inline UI:
- Use only self.inline_send / self.inline_buttons
- Do not use Telethon Button.inline/Button.url directly
- Do not create your own callback router
- Do not call event.client.add_event_handler from module code

Required inline pattern:
await self.inline_send(
    event,
    text,
    reply_markup=self.inline_buttons(
        [{"text": "Open", "url": "https://..."}],
        [{"text": "Next", "callback": self.next_callback, "args": (arg1,)}],
        [{"text": "Back", "callback": self.back_callback, "args": (arg1,)}],
        [{"text": "Close", "callback": self.close_callback, "args": ()}],
    ),
    parse_mode="html",
    link_preview=False,
    ttl=3600,
)

5) Callback handlers:
- First argument must be call
- Example:
  async def next_callback(self, call, arg1):
      await call.edit("...", reply_markup=self.inline_buttons(...))
- Close handler:
  async def close_callback(self, call):
      await call.edit("Closed.", reply_markup=None)

6) Security and forbidden actions:
- Do not read BOT_TOKEN
- Do not create a bot client
- Do not send messages via bot client
- Do not hardcode secrets or tokens

7) If callback must send files/media:
- Use call.original_client and call.original_chat_id
- Do not rely on call.chat_id for origin chat

8) Optional metadata for DeathTG web panel:
- Add near top of module:
  # meta developer: @username
  # meta banner: https://.../banner.png
  # scope: inline
  # requires: aiohttp
- If repository has Image.png near the module file, DeathTG can use it as card background.

9) Code style:
- ASCII-friendly code
- clear names
- minimal dependencies
- no mojibake

Output:
- Return one full ready-to-use .py module file only.
```
