#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVER_CMD="${SERVER_CMD:-python app.py}"
TUNNEL_CMD="${TUNNEL_CMD:-${PROJECT_ROOT}/communicationPlane/publicTrafficHandler/tunnel_api.sh}"

if command -v python >/dev/null 2>&1; then
  ENV_PATH="${PROJECT_ROOT}/env"
  if [ -f "${ENV_PATH}" ]; then
    export TUNNEL_TOKEN="${TUNNEL_TOKEN:-$(ENV_PATH="${ENV_PATH}" python - <<'PY'
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
)}"
  fi
fi

start_server() {
  (cd "${PROJECT_ROOT}/communicationPlane/server" && ${SERVER_CMD}) &
  echo $!
}

start_tunnel() {
  ${TUNNEL_CMD} &
  echo $!
}

SERVER_PID="$(start_server)"
TUNNEL_PID=""

if pgrep -f "cloudflared.*tunnel run" >/dev/null 2>&1; then
  echo "Tunnel already running. Skipping tunnel start."
else
  TUNNEL_PID="$(start_tunnel)"
fi

echo "Server PID: ${SERVER_PID}"
if [ -n "${TUNNEL_PID}" ]; then
  echo "Tunnel PID: ${TUNNEL_PID}"
else
  echo "Tunnel PID: (using existing tunnel)"
fi

cleanup() {
  if [ -n "${SERVER_PID}" ]; then
    kill "${SERVER_PID}" 2>/dev/null || true
  fi
  if [ -n "${TUNNEL_PID}" ]; then
    kill "${TUNNEL_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

while true; do
  if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "Server stopped. Shutting down tunnel."
    break
  fi
  if [ -n "${TUNNEL_PID}" ]; then
    if ! kill -0 "${TUNNEL_PID}" 2>/dev/null; then
      echo "Tunnel stopped. Shutting down server."
      break
    fi
  fi
  sleep 1
done
