#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARSER="$ROOT_DIR/software/mikrotik_dhcp_parser.py"

if [[ ! -f "$PARSER" ]]; then
  echo "[ERROR] parser not found: $PARSER" >&2
  exit 1
fi

cp "$PARSER" "$PARSER.bak.$(date +%F_%H-%M-%S)"

python3 - "$PARSER" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

# Extra DHCP option lines in MikroTik detailed DHCP logs are multiline and do not always
# contain the word "dhcp". Older parser skipped them before Host-Name/Client-Id/chaddr
# could be merged into the router session.
if "ADDRESS_REQUEST_RE" not in s:
    s = s.replace(
        'REQUEST_IP_RE = re.compile(rf"\\breceived request id \\d+ from (?P<ip>{IP_PATTERN})", re.IGNORECASE)\n',
        'REQUEST_IP_RE = re.compile(rf"\\breceived request id \\d+ from (?P<ip>{IP_PATTERN})", re.IGNORECASE)\n'
        'ADDRESS_REQUEST_RE = re.compile(rf"\\bAddress-Request\\s*=\\s*(?P<ip>{IP_PATTERN})", re.IGNORECASE)\n'
        'YIADDR_RE = re.compile(rf"\\byiaddr\\s*=\\s*(?P<ip>{IP_PATTERN})", re.IGNORECASE)\n'
        'PARAMETER_LIST_RE = re.compile(r"\\bParameter-List\\s*=\\s*\\\"?(?P<value>[^\\r\\n]+)", re.IGNORECASE)\n'
    )

old_guard = '    if not text or "dhcp" not in text.lower():\n        return None\n'
new_guard = '''    if not text:
        return None

    dhcp_option_line = any(x in text for x in (
        "Host-Name",
        "hostname",
        "host-name",
        "Client-Id",
        "Class-Id",
        "Parameter-List",
        "Address-Request",
        "ciaddr",
        "chaddr",
        "yiaddr",
    ))

    if "dhcp" not in text.lower() and not dhcp_option_line:
        return None
'''
if old_guard in s and new_guard not in s:
    s = s.replace(old_guard, new_guard)

old_ip_block = '''    ciaddr_match = CIADDR_RE.search(text)
    ciaddr_ip = ciaddr_match.group("ip") if ciaddr_match else None

    if client_id or class_id or host_name or chaddr_mac or ciaddr_ip or request_ip:'''
new_ip_block = '''    ciaddr_match = CIADDR_RE.search(text)
    ciaddr_ip = ciaddr_match.group("ip") if ciaddr_match else None
    address_request_match = ADDRESS_REQUEST_RE.search(text)
    address_request_ip = address_request_match.group("ip") if address_request_match else None
    yiaddr_match = YIADDR_RE.search(text)
    yiaddr_ip = yiaddr_match.group("ip") if yiaddr_match else None
    parameter_list = extract_value(PARAMETER_LIST_RE, text)

    if client_id or class_id or host_name or chaddr_mac or ciaddr_ip or request_ip or address_request_ip or yiaddr_ip or parameter_list:'''
if old_ip_block in s and new_ip_block not in s:
    s = s.replace(old_ip_block, new_ip_block)

old_session_ip = '''        if ciaddr_ip:
            session["ip"] = ciaddr_ip
        if request_ip:
            session["ip"] = request_ip'''
new_session_ip = '''        if ciaddr_ip and ciaddr_ip != "0.0.0.0":
            session["ip"] = ciaddr_ip
        if request_ip and request_ip != "0.0.0.0":
            session["ip"] = request_ip
        if address_request_ip and address_request_ip != "0.0.0.0":
            session["ip"] = address_request_ip
        if yiaddr_ip and yiaddr_ip != "0.0.0.0":
            session["ip"] = yiaddr_ip'''
if old_session_ip in s and new_session_ip not in s:
    s = s.replace(old_session_ip, new_session_ip)

old_return_guard = '    if (class_id or client_id or host_name) and session.get("mac"):'
new_return_guard = '    if (class_id or client_id or host_name or address_request_ip or yiaddr_ip or parameter_list) and session.get("mac"):'
if old_return_guard in s and new_return_guard not in s:
    s = s.replace(old_return_guard, new_return_guard)

p.write_text(s, encoding="utf-8")
print(f"[OK] patched {p}")
PY

python3 -m py_compile "$PARSER"
echo "[OK] syntax check passed"
