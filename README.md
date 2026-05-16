# DeathTG

```text
█████████████████████████████████████████████████████████████████████████████████████████████████████████████
█░░░░░░░░░░░░███░░░░░░░░░░░░░░█░░░░░░░░░░░░░░█░░░░░░░░░░░░░░█░░░░░░██░░░░░░████░░░░░░░░░░░░░░█░░░░░░░░░░░░░░█
█░░▄▀▄▀▄▀▄▀░░░░█░░▄▀▄▀▄▀▄▀▄▀░░█░░▄▀▄▀▄▀▄▀▄▀░░█░░▄▀▄▀▄▀▄▀▄▀░░█░░▄▀░░██░░▄▀░░████░░▄▀▄▀▄▀▄▀▄▀░░█░░▄▀▄▀▄▀▄▀▄▀░░█
█░░▄▀░░░░▄▀▄▀░░█░░▄▀░░░░░░░░░░█░░▄▀░░░░░░▄▀░░█░░░░░░▄▀░░░░░░█░░▄▀░░██░░▄▀░░████░░░░░░▄▀░░░░░░█░░▄▀░░░░░░░░░░█
█░░▄▀░░██░░▄▀░░█░░▄▀░░█████████░░▄▀░░██░░▄▀░░█████░░▄▀░░█████░░▄▀░░██░░▄▀░░████████░░▄▀░░█████░░▄▀░░█████████
█░░▄▀░░██░░▄▀░░█░░▄▀░░░░░░░░░░█░░▄▀░░░░░░▄▀░░█████░░▄▀░░█████░░▄▀░░░░░░▄▀░░████████░░▄▀░░█████░░▄▀░░█████████
█░░▄▀░░██░░▄▀░░█░░▄▀▄▀▄▀▄▀▄▀░░█░░▄▀▄▀▄▀▄▀▄▀░░█████░░▄▀░░█████░░▄▀▄▀▄▀▄▀▄▀░░████████░░▄▀░░█████░░▄▀░░██░░░░░░█
█░░▄▀░░██░░▄▀░░█░░▄▀░░░░░░░░░░█░░▄▀░░░░░░▄▀░░█████░░▄▀░░█████░░▄▀░░░░░░▄▀░░████████░░▄▀░░█████░░▄▀░░██░░▄▀░░█
█░░▄▀░░██░░▄▀░░█░░▄▀░░█████████░░▄▀░░██░░▄▀░░█████░░▄▀░░█████░░▄▀░░██░░▄▀░░████████░░▄▀░░█████░░▄▀░░██░░▄▀░░█
█░░▄▀░░░░▄▀▄▀░░█░░▄▀░░░░░░░░░░█░░▄▀░░██░░▄▀░░█████░░▄▀░░█████░░▄▀░░██░░▄▀░░████████░░▄▀░░█████░░▄▀░░░░░░▄▀░░█
█░░▄▀▄▀▄▀▄▀░░░░█░░▄▀▄▀▄▀▄▀▄▀░░█░░▄▀░░██░░▄▀░░█████░░▄▀░░█████░░▄▀░░██░░▄▀░░████████░░▄▀░░█████░░▄▀▄▀▄▀▄▀▄▀░░█
█░░░░░░░░░░░░███░░░░░░░░░░░░░░█░░░░░░██░░░░░░█████░░░░░░█████░░░░░░██░░░░░░████████░░░░░░█████░░░░░░░░░░░░░░█
█████████████████████████████████████████████████████████████████████████████████████████████████████████████
```

DeathTG is a secure Telegram userbot platform with:

- web-first setup
- trusted multi-device panel access
- Telegram-delivered secure links
- update notifications in bot instead of silent auto-update
- module cards with images on the website
- folder-based module architecture

## One-Line Install

For Linux / VPS / Oracle Cloud:

```bash
clear && cd ~ && rm -rf DeathTG && git clone https://github.com/Error4ikUa/DeathTG.git && cd DeathTG && python3 bootstrap.py
```

What happens automatically:

1. Creates `.venv`
2. Installs `pip` updates
3. Installs `requirements.txt`
4. Starts `dtg.py`
5. Prints your setup link in terminal
6. Opens DeathTG web setup flow

## First Start Flow

You do not need manual `nano .env`.

On first start:

1. Open the setup link from terminal
2. Enter `API_ID`
3. Enter `API_HASH`
4. Enter Telegram phone number
5. Enter Telegram code
6. Enter 2FA password if enabled

After that DeathTG:

1. Creates Telegram session
2. Starts userbot automatically
3. Creates / syncs bots
4. Sends welcome message in Telegram
5. Sends your personal secure panel links

## Public Server Logic

DeathTG is designed for normal environments:

- Ubuntu / Debian VPS
- Oracle Cloud free server
- Linux desktop
- Windows PowerShell
- Windows CMD

DeathTG blocks unsupported Android terminal environments like Termux.

For public servers:

- setup is protected with setup-token
- remote password login is restricted
- access for phone / PC / tablet goes through secure signed device links
- multiple trusted devices can exist at once
- each device can be revoked separately

## Update Logic

DeathTG does not auto-update from GitHub behind the user.

Instead it:

1. checks repository state
2. detects when a new update exists
3. sends update notification in Telegram
4. shows buttons like `Update` / `Ignore`
5. offers restart only after successful update

## Module System

DeathTG supports two local module formats.

Single-file module:

```text
modules/MyModule.py
```

Folder module:

```text
modules/MyModule/
modules/MyModule/MyModule.py
modules/MyModule/main.py
modules/MyModule/__init__.py
modules/MyModule/Module.png
```

Rules:

- module can be `.py` file or folder
- module folder can contain `MyModule.py`, `main.py`, or `__init__.py`
- module image should be `Module.png`
- recommended image ratio is `16:9`

Fallback image logic:

- first: module local `Module.png`
- second: `images/modules/<module_name>.png`
- third: `images/modules/Module.png`

## Image Contract

Put your PNG assets in `images/` with these exact names:

- `welcome_deathtg.png`
- `update_available_deathtg.png`
- `creating_backup.png`

Module images:

- `images/modules/Module.png`
- `images/modules/<module_name>.png`

## Profile / Info Text

Profile editor supports:

- Unicode text
- custom info text
- Telegram premium emoji HTML in info caption

Example:

```html
<emoji id="1234567890123456789">⭐️</emoji> {title}
```

`info_text` can use placeholders like:

- `{title}`
- `{name}`
- `{username}`
- `{description}`
- `{role}`
- `{prefix}`
- `{uses}`
- `{days}`
- `{level}`
- `{level_current}`
- `{top_modules}`
- `{version}`
- `{branch}`

## Useful Commands

Run directly:

```bash
python dtg.py
```

Cross-platform bootstrap:

```bash
python3 bootstrap.py
```

Windows PowerShell:

```powershell
.\bootstrap.ps1
```

Windows CMD:

```cmd
bootstrap.cmd
```

Server bootstrap:

```bash
bash scripts/install_server.sh
```

Public HTTPS-ready VPS bootstrap:

```bash
DTG_PUBLIC_HOST=panel.example.com DTG_PUBLIC_URL=https://panel.example.com bash scripts/install_server.sh
```

## Security Notes

- keep `.env` private
- keep `*.session` private
- do not share your secure panel links
- do not expose plain HTTP publicly
- use HTTPS for public panel access
- use trusted device links instead of sharing panel password

## Docs

- `docs/server_deploy.md`
- `images/README.md`
