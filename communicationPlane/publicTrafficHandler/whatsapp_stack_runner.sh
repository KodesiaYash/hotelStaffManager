#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STACK_CMD="${STACK_CMD:-${PROJECT_ROOT}/communicationPlane/publicTrafficHandler/start_whatsapp_stack.sh}"
RESTART_DELAY="${RESTART_DELAY:-3}"
MAX_RESTART_DELAY="${MAX_RESTART_DELAY:-30}"

if [ ! -x "${STACK_CMD}" ]; then
  echo "Stack command not executable: ${STACK_CMD}"
  exit 1
fi

delay="${RESTART_DELAY}"

while true; do
  echo "Starting WhatsApp stack (server + tunnel)."
  set +e
  output="$(bash "${STACK_CMD}" 2>&1)"
  status=$?
  set -e

  echo "${output}"

  if echo "${output}" | grep -q "Nothing to do"; then
    echo "Stack already running. Exiting supervisor."
    exit 0
  fi

  echo "Stack exited (status ${status}). Restarting in ${delay}s."
  sleep "${delay}"

  if [ "${delay}" -lt "${MAX_RESTART_DELAY}" ]; then
    delay=$((delay * 2))
    if [ "${delay}" -gt "${MAX_RESTART_DELAY}" ]; then
      delay="${MAX_RESTART_DELAY}"
    fi
  fi
done
