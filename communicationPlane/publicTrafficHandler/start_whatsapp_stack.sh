#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_PATH="${PROJECT_ROOT}/env"
SERVER_CMD="${SERVER_CMD:-python app.py}"
SERVER_HOST="${SERVER_HOST:-}"
SERVER_PORT="${SERVER_PORT:-}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-cloudflared}"
TUNNEL_TOKEN="${TUNNEL_TOKEN:-}"
TUNNEL_PROCESS_MATCH="${TUNNEL_PROCESS_MATCH:-cloudflared.*tunnel run}"

read_env_value() {
  local key="$1"
  if [ ! -f "${ENV_PATH}" ]; then
    echo ""
    return
  fi
  if command -v python >/dev/null 2>&1; then
    ENV_PATH="${ENV_PATH}" KEY="${key}" python - <<'PY'
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
    return
  fi

  local line
  line=$(grep -E "^\s*${key}\s*=" "${ENV_PATH}" | head -n1 || true)
  if [ -z "${line}" ]; then
    echo ""
    return
  fi
  echo "${line#*=}" | sed -e 's/^\s*//' -e 's/\s*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

if [ -z "${SERVER_HOST}" ]; then
  SERVER_HOST="$(read_env_value SERVER_HOST)"
fi
if [ -z "${SERVER_PORT}" ]; then
  SERVER_PORT="$(read_env_value SERVER_PORT)"
fi
if [ -z "${TUNNEL_TOKEN}" ]; then
  TUNNEL_TOKEN="$(read_env_value TUNNEL_TOKEN)"
fi

SERVER_HOST="${SERVER_HOST:-127.0.0.1}"
SERVER_PORT="${SERVER_PORT:-5050}"

server_is_running() {
  if command -v python >/dev/null 2>&1; then
    SERVER_HOST="${SERVER_HOST}" SERVER_PORT="${SERVER_PORT}" python - <<'PY'
import os
import sys
from http.client import HTTPConnection

host = os.environ.get("SERVER_HOST", "127.0.0.1")
port = int(os.environ.get("SERVER_PORT", "5050"))
conn = HTTPConnection(host, port, timeout=0.4)
try:
    conn.request("GET", "/health")
    resp = conn.getresponse()
    sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
finally:
    try:
        conn.close()
    except Exception:
        pass
PY
    return $?
  fi
  return 1
}

tunnel_is_running() {
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -f "${TUNNEL_PROCESS_MATCH}" >/dev/null 2>&1
    return $?
  fi
  return 1
}

SERVER_PID=""
TUNNEL_PID=""
SERVER_STARTED=0
TUNNEL_STARTED=0

if server_is_running; then
  echo "Server already running. Skipping server start."
else
  (
    cd "${PROJECT_ROOT}/communicationPlane/server" \
      && SERVER_HOST="${SERVER_HOST}" SERVER_PORT="${SERVER_PORT}" ${SERVER_CMD}
  ) &
  SERVER_PID=$!
  SERVER_STARTED=1
fi

if tunnel_is_running; then
  echo "Tunnel already running. Skipping tunnel start."
else
  if [ -z "${TUNNEL_TOKEN}" ]; then
    echo "TUNNEL_TOKEN missing. Skipping tunnel start." >&2
  else
    if ! command -v "${CLOUDFLARED_BIN}" >/dev/null 2>&1; then
      echo "cloudflared not found in PATH." >&2
    else
      "${CLOUDFLARED_BIN}" tunnel run --token "${TUNNEL_TOKEN}" &
      TUNNEL_PID=$!
      TUNNEL_STARTED=1
    fi
  fi
fi

if [ "${SERVER_STARTED}" -eq 0 ] && [ "${TUNNEL_STARTED}" -eq 0 ]; then
  echo "Server and tunnel already running. Nothing to do."
  exit 0
fi

if [ -n "${SERVER_PID}" ]; then
  echo "Server PID: ${SERVER_PID}"
else
  echo "Server PID: (using existing server)"
fi

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
  if [ -n "${SERVER_PID}" ]; then
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
      echo "Server stopped. Shutting down tunnel."
      break
    fi
  fi
  if [ -n "${TUNNEL_PID}" ]; then
    if ! kill -0 "${TUNNEL_PID}" 2>/dev/null; then
      echo "Tunnel stopped. Shutting down server."
      break
    fi
  fi
  sleep 1
done
