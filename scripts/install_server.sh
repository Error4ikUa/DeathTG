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
if [ -n "${DTG_PUBLIC_HOST:-}" ] || [ -n "${DTG_PUBLIC_URL:-}" ]; then
  echo "DeathTG panels are private-only. Do not configure a public domain; use Tailscale HTTPS."
  exit 2
fi

SUDO=""
if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

if command -v apt-get >/dev/null 2>&1; then
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y git python3 python3-venv python3-pip
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
  --server-install
  --panel-host 127.0.0.1
  --panel-port "${PANEL_PORT}"
  --service-file "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.service"
)

python "${BOOTSTRAP_ARGS[@]}"

if command -v systemctl >/dev/null 2>&1; then
  ${SUDO} cp "${INSTALL_DIR}/runtime/deploy/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
  ${SUDO} systemctl daemon-reload
  ${SUDO} systemctl enable "${SERVICE_NAME}"
  ${SUDO} systemctl restart "${SERVICE_NAME}"
fi

echo
echo "DeathTG server bootstrap complete."
echo "Install Tailscale on your PC or phone and sign in to your account."
if command -v systemctl >/dev/null 2>&1; then
  echo "Follow the private setup link printed by DeathTG: sudo journalctl -u ${SERVICE_NAME} -f"
else
  echo "Start the server flow in this console: cd \"${INSTALL_DIR}\" && .venv/bin/python dtg.py --server"
fi
echo "The server setup link appears only after Tailscale authorization succeeds."
