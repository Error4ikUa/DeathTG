#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOFTWARE_DIR="$ROOT_DIR/software"
GRAFANA_DIR="$SOFTWARE_DIR/grafana"
DB_DIR="$SOFTWARE_DIR/data/db"
DB_FILE="$DB_DIR/devices.db"
ENV_FILE="$SOFTWARE_DIR/.env"

say() { echo "[NetFinger] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; exit 1; }

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif sudo docker compose version >/dev/null 2>&1; then
    sudo docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    err "Docker Compose not found. Run ./install.sh first."
  fi
}

write_env_value() {
  local key="$1"
  local value="$2"
  python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

env_file = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = []
found = False
if env_file.exists():
    lines = env_file.read_text(encoding="utf-8", errors="ignore").splitlines()
for idx, line in enumerate(lines):
    if line.startswith(key + "="):
        lines[idx] = f"{key}={value}"
        found = True
        break
if not found:
    lines.append(f"{key}={value}")
env_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
PY
}

remove_env_key_pattern() {
  local pattern="$1"
  python3 - "$ENV_FILE" "$pattern" <<'PY'
from pathlib import Path
import re
import sys

env_file = Path(sys.argv[1])
pattern = re.compile(sys.argv[2])
if not env_file.exists():
    raise SystemExit(0)
lines = []
for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key and pattern.fullmatch(key):
        continue
    lines.append(line)
env_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
PY
}

[[ -d "$SOFTWARE_DIR" ]] || err "software/ directory not found. Run ./install.sh first."
[[ -d "$GRAFANA_DIR" ]] || err "software/grafana/ directory not found. Run ./install.sh first."
[[ -f "$SOFTWARE_DIR/mikrotik_push_receiver.py" ]] || err "missing mikrotik_push_receiver.py"
[[ -f "$SOFTWARE_DIR/mikrotik_dhcp_parser.py" ]] || err "missing mikrotik_dhcp_parser.py"

say "Stopping services..."
sudo systemctl stop rsyslog netfinger-grafana netfinger-enricher netfinger-mikrotik-push 2>/dev/null || true

say "Enforcing safe single-key Fingerbank mode..."
mkdir -p "$SOFTWARE_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'ENV'
FINGERBANK=0
FINGERBANK_API_KEY=
NETFINGER_ENRICH_INTERVAL=30
NETFINGER_ENRICH_LIMIT=300
NETFINGER_ENRICH_WORKERS=1
ENV
  chmod 600 "$ENV_FILE"
fi
write_env_value "FINGERBANK" "0"
write_env_value "NETFINGER_ENRICH_WORKERS" "1"
remove_env_key_pattern "FINGERBANK_API_KEYS"
remove_env_key_pattern "FINGERBANK_API_KEY_[0-9]+"

say "Applying multiline MikroTik DHCP parser fix if available..."
if [[ -x "$ROOT_DIR/fix_multiline_dhcp_parser.sh" ]]; then
  "$ROOT_DIR/fix_multiline_dhcp_parser.sh"
elif [[ -f "$ROOT_DIR/fix_multiline_dhcp_parser.sh" ]]; then
  chmod +x "$ROOT_DIR/fix_multiline_dhcp_parser.sh"
  "$ROOT_DIR/fix_multiline_dhcp_parser.sh"
else
  warn "fix_multiline_dhcp_parser.sh not found; continuing with current parser."
fi

say "Resetting SQLite database..."
mkdir -p "$DB_DIR"
if [[ -f "$DB_FILE" ]]; then
  cp -a "$DB_FILE" "$DB_FILE.bak.$(date +%F_%H-%M-%S)" || true
fi
rm -f "$DB_FILE" "$DB_FILE-wal" "$DB_FILE-shm"

say "Creating clean database schema..."
cd "$SOFTWARE_DIR"
if [[ ! -d venv ]]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
python3 -m pip install -r requirements.txt >/dev/null
DB_FILE="$DB_FILE" python3 - <<'PY'
import os
import sqlite3
from pathlib import Path
from mikrotik_push_receiver import ensure_schema

db_file = Path(os.environ["DB_FILE"])
db_file.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(db_file)
ensure_schema(conn)
conn.commit()
conn.close()
print(f"DB ready: {db_file}")
PY

say "Fixing permissions..."
sudo chgrp -R syslog "$DB_DIR" 2>/dev/null || true
sudo chmod 775 "$SOFTWARE_DIR" "$SOFTWARE_DIR/data" "$DB_DIR" 2>/dev/null || true
sudo chmod 664 "$DB_FILE" 2>/dev/null || true
sudo chgrp syslog "$ENV_FILE" 2>/dev/null || true
sudo chmod 640 "$ENV_FILE" 2>/dev/null || true
if command -v setfacl >/dev/null 2>&1; then
  sudo setfacl -m u:syslog:rwx "$DB_DIR" 2>/dev/null || true
  sudo setfacl -m u:syslog:rw "$DB_FILE" 2>/dev/null || true
  sudo setfacl -m u:syslog:r "$ENV_FILE" 2>/dev/null || true
  sudo setfacl -m u:472:rwx "$DB_DIR" 2>/dev/null || true
  sudo setfacl -m d:u:472:rwx "$DB_DIR" 2>/dev/null || true
  sudo setfacl -m u:472:rw "$DB_FILE" 2>/dev/null || true
fi

say "Starting rsyslog and Grafana..."
sudo systemctl daemon-reload
sudo systemctl start rsyslog
sudo systemctl start netfinger-grafana || true
cd "$GRAFANA_DIR"
compose up -d

say "Keeping Fingerbank enricher stopped for safety. Enable it only after unblock and one-key config."
sudo systemctl stop netfinger-enricher 2>/dev/null || true
sudo systemctl disable netfinger-enricher 2>/dev/null || true

SERVER_IP="$(hostname -I | awk '{print $1}')"
say "Done."
echo
echo "Grafana:  http://${SERVER_IP}:3000/d/netfinger-main"
echo "Local:    http://127.0.0.1:3000/d/netfinger-main"
echo "Login:    admin / admin"
echo "Syslog:   UDP 514"
echo "DB:       $DB_FILE"
echo
echo "Check DB: sqlite3 $DB_FILE \"select mac,ip,hostname,dhcp_class_id,dhcp_client_id,updated_at from devices order by updated_at desc limit 20;\""
echo "Logs:     sudo tail -f /var/log/netfinger-mikrotik-dhcp.log"
