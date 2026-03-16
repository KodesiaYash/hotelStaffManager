#!/usr/bin/env bash
set -euo pipefail

# Required env:
#   TUNNEL_TOKEN     - cloudflared tunnel token
#   TUNNEL_NAME      - tunnel name (e.g. hotelstaffmanager.prod)
#   TUNNEL_HOSTNAME  - public hostname to route (e.g. kodesia.tech)
# Optional:
#   OVERWRITE_DNS=1  - set to 1 to overwrite existing DNS record (default: 1)

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

read_env_var() {
  local key="$1"
  ENV_PATH="${PROJECT_ROOT}/env" KEY="${key}" python - <<'PY'
import os

env_path = os.environ.get("ENV_PATH", "env")
key = os.environ.get("KEY", "")

def simple_parse(path: str, env_key: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() != env_key:
                    continue
                return v.strip().strip('"').strip("'")
    except FileNotFoundError:
        return ""
    return ""

try:
    from dotenv import load_dotenv

    load_dotenv(env_path)
    print(os.getenv(key, ""))
except Exception:
    print(simple_parse(env_path, key))
PY
}

TUNNEL_TOKEN="${TUNNEL_TOKEN:-$(read_env_var TUNNEL_TOKEN)}"
TUNNEL_NAME="${TUNNEL_NAME:-$(read_env_var TUNNEL_NAME)}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-$(read_env_var TUNNEL_HOSTNAME)}"
OVERWRITE_DNS="${OVERWRITE_DNS:-1}"

TUNNEL_TOKEN="${TUNNEL_TOKEN:?TUNNEL_TOKEN is required}"
TUNNEL_NAME="${TUNNEL_NAME:?TUNNEL_NAME is required}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:?TUNNEL_HOSTNAME is required}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found."
  echo "Install with: brew install cloudflared"
  exit 1
fi

echo "Connecting DNS: ${TUNNEL_HOSTNAME} -> ${TUNNEL_NAME}"
set +e
if [ "${OVERWRITE_DNS}" = "1" ]; then
  cloudflared tunnel route dns --overwrite-dns "${TUNNEL_NAME}" "${TUNNEL_HOSTNAME}"
else
  cloudflared tunnel route dns "${TUNNEL_NAME}" "${TUNNEL_HOSTNAME}"
fi
DNS_STATUS=$?
set -e

if [ "${DNS_STATUS}" -ne 0 ]; then
  echo "Failed to create DNS route."
  echo "If this is your first time, run: cloudflared tunnel login"
  echo "Then retry this script."
  exit "${DNS_STATUS}"
fi

echo "Starting tunnel using token."
cloudflared tunnel run --token "${TUNNEL_TOKEN}"
