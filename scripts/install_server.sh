#!/usr/bin/env bash
set -euo pipefail

cat <<'EOF'
██████╗░███████╗░█████╗░████████╗██╗░░██╗  ████████╗░██████╗░
██╔══██╗██╔════╝██╔══██╗╚══██╔══╝██║░░██║  ╚══██╔══╝██╔════╝░
██║░░██║█████╗░░███████║░░░██║░░░███████║  ░░░██║░░░██║░░██╗░
██║░░██║██╔══╝░░██╔══██║░░░██║░░░██╔══██║  ░░░██║░░░██║░░╚██╗
██████╔╝███████╗██║░░██║░░░██║░░░██║░░██║  ░░░██║░░░╚██████╔╝
╚═════╝░╚══════╝╚═╝░░╚═╝░░░╚═╝░░░╚═╝░░╚═╝  ░░░╚═╝░░░░╚═════╝░
EOF
echo
echo "DeathTG secure server bootstrap"
echo

if [ -n "${TERMUX_VERSION:-}" ] || printf '%s' "${PREFIX:-}" | grep -qi "com.termux"; then
  echo "DeathTG server install is not supported on Termux."
  echo "Use Ubuntu/Debian on VPS, server, or desktop Linux."
  exit 1
fi

REPO_URL="${DTG_REPO_URL:-https://github.com/Error4ikUa/DeathTG.git}"
INSTALL_DIR="${DTG_INSTALL_DIR:-$HOME/DeathTG}"
SERVICE_NAME="${DTG_SERVICE_NAME:-deathtg}"
PANEL_PORT="${DTG_PANEL_PORT:-8080}"
PUBLIC_HOST="${DTG_PUBLIC_HOST:-}"
PUBLIC_URL="${DTG_PUBLIC_URL:-}"

if [ -n "${PUBLIC_HOST}" ] && [ -z "${PUBLIC_URL}" ]; then
  PUBLIC_URL="https://${PUBLIC_HOST}"
fi

SUDO=""
if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

if command -v apt-get >/dev/null 2>&1; then
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y git python3 python3-venv python3-pip
  if [ -n "${PUBLIC_HOST}" ]; then
    ${SUDO} apt-get install -y caddy || true
    ${SUDO} apt-get install -y nginx || true
  fi
fi

if [ -d "${INSTALL_DIR}/.git" ]; then
  git -C "${INSTALL_DIR}" fetch --all --prune
  git -C "${INSTALL_DIR}" pull --ff-only
else
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

cd "${INSTALL_DIR}"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt

BOOTSTRAP_ARGS=(
  -m deathtg.server_bootstrap
  --write-env
  --panel-host 127.0.0.1
  --panel-port "${PANEL_PORT}"
  --service-file "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.service"
)

if [ -n "${PUBLIC_HOST}" ]; then
  BOOTSTRAP_ARGS+=(--public-host "${PUBLIC_HOST}")
  BOOTSTRAP_ARGS+=(--nginx-file "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.nginx.conf")
  BOOTSTRAP_ARGS+=(--caddy-file "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.Caddyfile")
  BOOTSTRAP_ARGS+=(--server-name "${PUBLIC_HOST}")
fi

if [ -n "${PUBLIC_URL}" ]; then
  BOOTSTRAP_ARGS+=(--public-url "${PUBLIC_URL}")
fi

python "${BOOTSTRAP_ARGS[@]}"

if command -v systemctl >/dev/null 2>&1; then
  ${SUDO} cp "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
  ${SUDO} systemctl daemon-reload
  ${SUDO} systemctl enable "${SERVICE_NAME}"
  ${SUDO} systemctl restart "${SERVICE_NAME}"
fi

if [ -n "${PUBLIC_HOST}" ] && command -v caddy >/dev/null 2>&1; then
  ${SUDO} cp "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.Caddyfile" "/etc/caddy/Caddyfile"
  ${SUDO} systemctl restart caddy
elif [ -n "${PUBLIC_HOST}" ] && command -v nginx >/dev/null 2>&1; then
  ${SUDO} cp "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.nginx.conf" "/etc/nginx/sites-available/${SERVICE_NAME}"
  ${SUDO} ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
  ${SUDO} nginx -t
  ${SUDO} systemctl reload nginx
fi

echo
echo "DeathTG server bootstrap complete."
echo "Panel local URL: http://127.0.0.1:${PANEL_PORT}"
if [ -n "${PUBLIC_HOST}" ]; then
  echo "Public host prepared for HTTPS access: ${PUBLIC_HOST}"
fi
echo "Next step: open the panel and finish Telegram web setup."
