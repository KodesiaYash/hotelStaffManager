#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_PATH="${PROJECT_ROOT}/env"
PID_FILE="${PID_FILE:-${PROJECT_ROOT}/.cloudflared/tunnel.pid}"
LOG_FILE="${LOG_FILE:-${PROJECT_ROOT}/.cloudflared/tunnel.log}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-cloudflared}"
TUNNEL_TOKEN="${TUNNEL_TOKEN:-}"

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

if [ -z "${TUNNEL_TOKEN}" ]; then
  TUNNEL_TOKEN="$(read_env_value TUNNEL_TOKEN)"
fi

mkdir -p "$(dirname "${PID_FILE}")"

is_running() {
  if [ -f "${PID_FILE}" ]; then
    local pid
    pid="$(cat "${PID_FILE}")"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

start() {
  if is_running; then
    echo "Tunnel already running (pid $(cat "${PID_FILE}"))."
    return 0
  fi
  if [ -z "${TUNNEL_TOKEN}" ]; then
    echo "TUNNEL_TOKEN is required." >&2
    exit 1
  fi
  if ! command -v "${CLOUDFLARED_BIN}" >/dev/null 2>&1; then
    echo "cloudflared not found in PATH." >&2
    exit 1
  fi
  echo "Starting tunnel in background. Logs: ${LOG_FILE}"
  nohup "${CLOUDFLARED_BIN}" tunnel run --token "${TUNNEL_TOKEN}" >"${LOG_FILE}" 2>&1 &
  echo $! > "${PID_FILE}"
  echo "Tunnel started with pid $(cat "${PID_FILE}")."
}

stop() {
  if ! is_running; then
    echo "Tunnel not running."
    return 0
  fi
  pid="$(cat "${PID_FILE}")"
  echo "Stopping tunnel pid ${pid}."
  kill "${pid}" 2>/dev/null || true
  rm -f "${PID_FILE}"
}

status() {
  if is_running; then
    echo "Tunnel running (pid $(cat "${PID_FILE}"))."
  else
    echo "Tunnel not running."
  fi
}

case "${1:-start}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  status)
    status
    ;;
  restart)
    stop
    start
    ;;
  *)
    echo "Usage: $0 {start|stop|status|restart}"
    exit 1
    ;;
esac
