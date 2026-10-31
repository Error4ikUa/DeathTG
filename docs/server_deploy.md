# DeathTG Server Deploy

DeathTG now supports a secure-by-default server bootstrap:

- panel binds to `127.0.0.1` by default on desktops and servers
- private remote access uses the active Tailscale identity and requires no public domain
- a public bind is disabled unless `PANEL_ALLOW_REMOTE_BIND=1` is set explicitly
- strong `PANEL_SECRET` is auto-generated if missing or weak
- trusted hosts are enforced
- secure cookies switch on automatically when `PANEL_PUBLIC_URL=https://...`
- login/setup routes are rate-limited
- password login is removed from the active panel flow
- one-time signed device links can be created for phone, laptop, tablet, and other browsers
- `.env` and `.session*` files are tightened after write

## One-command install

```bash
bash scripts/install_server.sh
```

Cross-platform local bootstrap:

```bash
python3 bootstrap.py
```

Optional public reverse proxy:

```bash
DTG_PUBLIC_HOST=panel.example.com DTG_PUBLIC_URL=https://panel.example.com bash scripts/install_server.sh
```

During setup and Web mode, the panel binds only to `127.0.0.1`. Remote access
uses Tailscale Serve over HTTPS; DeathTG does not open a public or LAN listener.

On a server/VPS, the first run asks the operator to confirm that a main
computer or phone is already signed in to Tailscale. DeathTG installs and
starts Tailscale on the server, then prints the server authorization URL in
the console. Sign in to the same Tailscale account there. Once both devices
are online, press `Y` in the server console. DeathTG checks that the remote
device has the same Tailscale user ID as the server, configures HTTPS Serve,
and prints a one-time private setup URL. Both the tailnet identity and the
setup token are required to open it.

The setup page then offers Web dashboard or Telegram only. Web mode keeps the
Tailscale HTTPS route. Telegram-only mode uses the private setup page only for
the initial QR login, then disables the Serve route and closes the panel
listener while the userbot stays active. Tailscale may remain installed and
signed in, but no DeathTG website is available in this mode. On a local
computer, setup starts at localhost and Tailscale is only installed if Web
mode is selected.

To change the saved mode later, stop DeathTG and run `python dtg.py --choose-mode`.
On a server, this repeats the private Tailscale owner check before opening the
mode picker. A normal Telegram-only restart keeps the website closed.

On first successful start, DeathTG stores the local Tailscale user, tailnet,
MagicDNS suffix, and hostname in `runtime/tailscale_binding.json`. Later account
or tailnet changes fail closed for remote access while localhost keeps working.
Only peers owned by the same Tailscale user are accepted; shared tailnet users
do not receive automatic panel access.

Optional settings:

- `PANEL_TAILSCALE_SERVE=1` enables the private HTTPS Serve route.
- `PANEL_TAILSCALE_AUTO_SERVE=1` allows DeathTG to configure that route.
- `PANEL_TAILSCALE_EXPECTED_LOGIN=user@example.com` pins an additional expected login.

The installer:

- installs system packages when `apt-get` is available
- clones or updates the repo
- creates `.venv`
- installs Python dependencies
- generates secure env defaults
- writes a `systemd` unit to `runtime/deploy/`
- writes a `Caddyfile` for automatic HTTPS when `DTG_PUBLIC_HOST` is set
- writes an `nginx` config to `runtime/deploy/` when `DTG_PUBLIC_HOST` is set
- installs and restarts `systemd` / `caddy` or `nginx` when available
- enables background auto-update by default

## Recommended production shape

- Keep the FastAPI panel on `127.0.0.1`
- Put `caddy` or another TLS reverse proxy in front if you need public access
- Set `DTG_PUBLIC_URL=https://your-domain`
- Use bot/device grant links for daily login
- Treat one-time device links as the normal login method for phones and extra browsers
- Keep Telegram setup in the web flow, not in manual `.env` editing
- Do not use Termux or Android terminal environments for DeathTG deploys

## Health check

Local health endpoint:

```bash
curl http://127.0.0.1:8080/healthz
```
