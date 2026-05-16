# DeathTG

DeathTG is a Telethon-based userbot framework with a local control panel and inline module API.

## Current status

- Runtime control is terminal-first.
- Web panel is available, but currently intended for one trusted device/session at a time.
- Core modules and external modules use the same `Module` API.

## Quick start

1. Create and activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Configure `.env` (or open setup page first).
4. Start DeathTG:

```bash
python dtg.py
```

## Setup and login

- On first run open `/setup` and set:
  - `API_ID`
  - `API_HASH`
  - `PHONE`
  - `PANEL_PASSWORD`
  - `PANEL_SECRET`
- Panel login uses the password you set during setup.
- Session cookie is persistent (remember device behavior).

## Module author contract

Use:

- `from deathtg.loader import Module`
- `from deathtg.command import command`

Inline UI:

- `self.inline_send(...)`
- `self.inline_buttons(...)`
- `self.inline_form(...)`
- `self.inline_list(...)`
- `self.inline_gallery(...)`

Do not:

- read `BOT_TOKEN` from module code
- create your own bot client
- call `Button.inline` / `Button.url` directly
- register your own callback router

See docs:

- `docs/module_authoring.md`
- `docs/module_prompt_for_devs.md`

## Security notes

- Keep `.env` and `*.session` files private.
- Use a strong `PANEL_SECRET`.
- Keep `PANEL_PASSWORD` private.
- Do not expose panel publicly without HTTPS and network protection.
