# DeathTG

```text
██████╗  ███████╗ █████╗ ████████╗██╗  ██╗    ████████╗ ██████╗
██╔══██╗ ██╔════╝██╔══██╗╚══██╔══╝██║  ██║    ╚══██╔══╝██╔════╝
██║  ██║ █████╗  ███████║   ██║   ███████║       ██║   ██║  ███╗
██║  ██║ ██╔══╝  ██╔══██║   ██║   ██╔══██║       ██║   ██║   ██║
██████╔╝ ███████╗██║  ██║   ██║   ██║  ██║       ██║   ╚██████╔╝
╚═════╝  ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝       ╚═╝    ╚═════╝
```

DeathTG is a secure Telegram userbot platform with:

- web-first setup
- trusted multi-device panel access
- Telegram-delivered secure device links
- update notifications in bot instead of silent auto-update
- module cards with images on the website
- folder-based module architecture

## One-Line Install

For Ubuntu / WSL / VPS / Oracle Cloud:

```bash
clear && cd ~ && rm -rf DeathTG && git clone https://github.com/Error4ikUa/DeathTG.git && cd DeathTG && python3 bootstrap.py
```

For Windows PowerShell:

```powershell
Clear-Host; Set-Location $HOME; if (Test-Path DeathTG) { Remove-Item DeathTG -Recurse -Force }; git clone https://github.com/Error4ikUa/DeathTG.git; Set-Location DeathTG; py bootstrap.py
```

If port `8080` is already busy, use a different panel port:

```powershell
Clear-Host; Set-Location $HOME; if (Test-Path DeathTG) { Remove-Item DeathTG -Recurse -Force }; git clone https://github.com/Error4ikUa/DeathTG.git; Set-Location DeathTG; $env:PANEL_PORT="8090"; py bootstrap.py
```

What happens automatically:

1. Creates `.venv`
2. Updates `pip`
3. Installs `requirements.txt`
4. Starts `dtg.py`
5. Prints setup link in terminal
6. Opens DeathTG web setup flow

## Choose your control mode

On first start, DeathTG asks in the console whether it is running on a server or on your own computer.

- **Local computer:** setup opens at `http://127.0.0.1`. Choose Telegram only to use the page just for Telegram QR sign-in; the website closes when setup finishes. Choose Web dashboard to install/start Tailscale and create a private HTTPS address. Add your other devices to the same Tailscale account to open it.
- **Server/VPS:** before showing a setup page, DeathTG connects the server to Tailscale. Install Tailscale on your main computer or phone, sign in, then approve the Tailscale link printed in the server console with that same account. Return to the console and press `Y`; DeathTG verifies the server and your device share the same Tailscale owner. It then prints a private, one-time `https://…ts.net/setup?setup_token=…` link. Open it from a device already signed in to that account.
- **Choose a mode on the setup page:** Web dashboard keeps the site on private Tailscale HTTPS. Telegram only turns off the HTTPS route and closes the setup website after QR sign-in; DeathTG keeps running in Telegram and the console. The userbot still sends its welcome message, without an unusable panel link.

Tailscale account sign-in may use Google if you chose Google as the identity provider when creating that Tailscale account. DeathTG itself does not collect Google credentials or provide a separate hosted account. Access is restricted to the Tailscale identity bound to this DeathTG installation and devices owned by that same Tailscale user. The one-time setup token is an additional gate, not a replacement for Tailscale.

The setup site binds to localhost. Remote access is through Tailscale Serve HTTPS; it is not opened on the public internet. An SSH tunnel is only a manual troubleshooting fallback, not the normal server setup path.

To reopen the setup mode picker later, stop the running DeathTG process and start `python dtg.py --choose-mode`. On a server this runs the private Tailscale check again before showing the picker.

## First Start

You do not need manual `nano .env`.

1. Open the setup link from terminal
2. Enter `API_ID`
3. Enter `API_HASH`
4. Scan the Telegram QR code
5. Enter 2FA password only if Telegram asks for it

After that DeathTG:

1. Creates Telegram session
2. Starts userbot automatically
3. Syncs bots
4. Sends a welcome message in Telegram; Web mode includes a personal secure panel link, Telegram-only mode does not include a website link

## Public Server Logic

Supported environments:

- Ubuntu / Debian VPS
- Oracle Cloud free server
- Linux desktop
- Windows PowerShell
- Windows CMD

Blocked or limited:

- Termux
- Android terminal environments
- WSL for phone/public panel access (use only for local development on the same PC)

For public servers DeathTG uses:

- setup-token protection
- trusted multi-device sessions
- secure signed one-time device links
- trusted secure-link login for remote devices
- per-device revoke

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
- folder module can use `ModuleName.py`, `main.py`, or `__init__.py`
- module image should be `Module.png`
- recommended image ratio is `16:9`

Fallback image logic:

1. module local `Module.png`
2. `images/modules/<module_name>.png` or `Image/modules/<module_name>.png`
3. `images/modules/Module.png` or `Image/modules/Module.png`

## DTG_Modules Repo Contract

For repository modules in `DTG_Modules` the normal format is:

```text
ModuleName/
ModuleName/ModuleName.py
ModuleName/Module.png
```

If `Module.png` is missing, DeathTG shows the shared fallback from `images/modules/Module.png` or `Image/modules/Module.png`.

## Image Contract

Put your PNG assets in `images/` or `Image/` with these exact names:

- `DeathTG_welcome.png`
- `DeathTG_update_available.png`
- `DeathTG_creating_backup.png`

Module images:

- `images/modules/Module.png`
- `images/modules/<module_name>.png`

## Useful Commands

Run directly:

```bash
python dtg.py
```

Bootstrap:

```bash
python3 bootstrap.py
```

PowerShell:

```powershell
.\bootstrap.ps1
```

CMD:

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
- do not share secure panel links
- do not expose plain HTTP publicly
- prefer HTTPS for public panel access
- use trusted one-device links instead of passwords

## Docs

- `docs/server_deploy.md`
- `images/README.md`

## License

DeathTG is licensed under AGPL-3.0-or-later.

Copyright (C) 2026 DeathTerror.

Hikka was used as an architectural reference for module-loading ideas. Hikka is AGPL-3.0 and remains credited in `NOTICE`.
