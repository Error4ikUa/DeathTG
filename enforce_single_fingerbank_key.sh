#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/software/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  mkdir -p "$(dirname "$ENV_FILE")"
  cat > "$ENV_FILE" <<'ENV'
FINGERBANK=0
FINGERBANK_API_KEY=
NETFINGER_ENRICH_INTERVAL=30
NETFINGER_ENRICH_LIMIT=300
NETFINGER_ENRICH_WORKERS=1
ENV
fi

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import re
import sys

env = Path(sys.argv[1])
lines = env.read_text(encoding="utf-8", errors="ignore").splitlines()
values = {}
for line in lines:
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    values[k.strip()] = v.strip().strip('"').strip("'")

# Keep exactly one key. Prefer FINGERBANK_API_KEY, fall back to the first old multi-key entry.
single = values.get("FINGERBANK_API_KEY", "").strip()
if not single:
    multi = values.get("FINGERBANK_API_KEYS", "")
    for item in re.split(r"[,;]", multi):
        item = item.strip()
        if item:
            single = item
            break
if not single:
    for name in sorted(values):
        if re.fullmatch(r"FINGERBANK_API_KEY_\d+", name):
            value = values.get(name, "").strip()
            if value:
                single = value
                break

new = []
seen = set()
removed_patterns = (
    re.compile(r"FINGERBANK_API_KEYS"),
    re.compile(r"FINGERBANK_API_KEY_\d+"),
    re.compile(r"NETFINGER_ENRICH_WORKERS"),
)
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key and any(p.fullmatch(key) for p in removed_patterns):
        continue
    if key == "FINGERBANK_API_KEY":
        if key not in seen:
            new.append(f"FINGERBANK_API_KEY={single}")
            seen.add(key)
        continue
    new.append(line)
    if key:
        seen.add(key)

if "FINGERBANK" not in seen:
    new.append("FINGERBANK=0")
if "FINGERBANK_API_KEY" not in seen:
    new.append(f"FINGERBANK_API_KEY={single}")
if "NETFINGER_ENRICH_WORKERS" not in seen:
    new.append("NETFINGER_ENRICH_WORKERS=1")

env.write_text("\n".join(new).strip() + "\n", encoding="utf-8")
print("[OK] .env now uses one Fingerbank key only")
print("[OK] NETFINGER_ENRICH_WORKERS=1")
PY

# Patch install.sh locally after pull if old multi-key installer logic is still present.
INSTALL="$ROOT_DIR/install.sh"
if [[ -f "$INSTALL" ]]; then
  cp "$INSTALL" "$INSTALL.bak.single-key.$(date +%F_%H-%M-%S)"
  python3 - "$INSTALL" <<'PY'
from pathlib import Path
import re
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8", errors="ignore")

# Make generated default .env single-key and low-concurrency.
s = s.replace("FINGERBANK_API_KEYS=\nNETFINGER_ENRICH_INTERVAL=30\nNETFINGER_ENRICH_LIMIT=300\nNETFINGER_ENRICH_WORKERS=8",
              "FINGERBANK_API_KEY=\nNETFINGER_ENRICH_INTERVAL=30\nNETFINGER_ENRICH_LIMIT=300\nNETFINGER_ENRICH_WORKERS=1")
s = s.replace("NETFINGER_ENRICH_WORKERS=8", "NETFINGER_ENRICH_WORKERS=1")

# If installer writes new key to old multi-key variable, redirect it to single-key.
s = s.replace('write_env_value "FINGERBANK_API_KEYS" "$key"', 'write_env_value "FINGERBANK_API_KEY" "$key"')
s = s.replace('write_env_value "FINGERBANK_API_KEY" ""', '# single-key mode: keep FINGERBANK_API_KEY only')

# Remove old key-rotation style env variables whenever a new key is set.
needle = 'remove_env_key_pattern "FINGERBANK_API_KEY_[0-9]+"'
if needle in s and 'remove_env_key_pattern "FINGERBANK_API_KEYS"' not in s:
    s = s.replace(needle, needle + '\n    remove_env_key_pattern "FINGERBANK_API_KEYS"')

# Rename visible wording so nobody thinks multi-key mode is supported.
s = s.replace("Saved 1 Fingerbank API key.", "Saved one Fingerbank API key.")
s = s.replace("Older `.env` files with multiple keys are still readable, but replacing the key through the installer collapses the config back to one key.",
              "Only one Fingerbank API key is supported per external IP address.")

p.write_text(s, encoding="utf-8")
print("[OK] install.sh patched for single-key local mode")
PY
fi

# Patch common Python files if they still read FINGERBANK_API_KEYS as a list.
for f in "$ROOT_DIR"/software/*.py; do
  [[ -f "$f" ]] || continue
  if grep -q "FINGERBANK_API_KEYS\|FINGERBANK_API_KEY_" "$f"; then
    cp "$f" "$f.bak.single-key.$(date +%F_%H-%M-%S)"
    python3 - "$f" <<'PY'
from pathlib import Path
import re
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8", errors="ignore")

# Best-effort hardening: old list variables are neutralized to a single key env.
s = s.replace('os.getenv("FINGERBANK_API_KEYS", "")', 'os.getenv("FINGERBANK_API_KEY", "")')
s = s.replace("os.getenv('FINGERBANK_API_KEYS', '')", "os.getenv('FINGERBANK_API_KEY', '')")
s = re.sub(r'FINGERBANK_API_KEY_\{?\w+\}?', 'FINGERBANK_API_KEY', s)

p.write_text(s, encoding="utf-8")
print(f"[OK] hardened {p}")
PY
  fi
done

find "$ROOT_DIR" -type f \( -name '*.py' -o -name '*.sh' -o -name '*.md' \) \
  -not -path '*/.git/*' \
  -exec grep -HnE 'FINGERBANK_API_KEYS|FINGERBANK_API_KEY_[0-9]+' {} + || true

echo "[DONE] Single-key Fingerbank mode enforced locally. Review grep output above: only comments/backups should remain."
