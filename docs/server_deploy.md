# DeathTG Server Deploy

DeathTG now supports a secure-by-default server bootstrap:

- panel binds to the configured local/LAN host
- public remote access is intended for `https://` only
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
