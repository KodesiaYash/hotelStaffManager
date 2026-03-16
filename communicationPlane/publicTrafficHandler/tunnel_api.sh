#!/usr/bin/env bash
set -euo pipefail

# Required env:
#   TUNNEL_TOKEN - cloudflared tunnel token

if [ -z "${TUNNEL_TOKEN:-}" ] && command -v python >/dev/null 2>&1; then
  ENV_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/env"
  if [ -f "${ENV_PATH}" ]; then
    TUNNEL_TOKEN="$(ENV_PATH="${ENV_PATH}" python - <<'PY'
import os

env_path = os.environ.get("ENV_PATH", "env")

def simple_parse(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() != "TUNNEL_TOKEN":
                    continue
                return value.strip().strip('"').strip("'")
    except FileNotFoundError:
        return ""
    return ""

try:
    from dotenv import load_dotenv

    load_dotenv(env_path)
    print(os.getenv("TUNNEL_TOKEN", ""))
except Exception:
    print(simple_parse(env_path))
PY
)"
  fi
fi

TUNNEL_TOKEN="${TUNNEL_TOKEN:?TUNNEL_TOKEN is required}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found."
  echo "Install with: brew install cloudflared"
  exit 1
fi

echo "Starting tunnel using token."
cloudflared tunnel run --token "${TUNNEL_TOKEN}"
