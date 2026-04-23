#!/usr/bin/env bash
# Deploy hotelStaffManager onto the host that runs this script (e.g. the Mac
# mini). Designed to be invoked by a self-hosted GitHub Actions runner, but
# safe to run manually as well.
#
# The "production" checkout lives at a fixed path (APP_DIR). This script
# fetches the latest commit on the deploy branch, rebuilds the app container,
# restarts it, and verifies the health endpoint. The `env` / `envConfig/`
# files are NOT tracked in git and must already exist inside APP_DIR.

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/Desktop/DeploymentHost/hotelStaffManager}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-app}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:5050/health}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-60}"

log() { printf '[deploy %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
fail() { printf '[deploy ERROR] %s\n' "$*" >&2; exit 1; }

# ── Preflight ───────────────────────────────────────────────────────────────
command -v docker >/dev/null || fail "docker is not installed or not in PATH"
docker compose version >/dev/null || fail "docker compose plugin is not available"

[ -d "$APP_DIR/.git" ] || fail "$APP_DIR is not a git checkout. Clone the repo there first."
[ -f "$APP_DIR/env" ] || fail "$APP_DIR/env is missing. Create it before deploying (secrets)."
[ -f "$APP_DIR/docker-compose.yml" ] || fail "$APP_DIR/docker-compose.yml is missing."

cd "$APP_DIR"

# ── Fetch latest code ───────────────────────────────────────────────────────
log "Fetching latest origin/$DEPLOY_BRANCH"
git fetch --prune --tags origin
git checkout "$DEPLOY_BRANCH"
BEFORE_SHA="$(git rev-parse HEAD)"
git reset --hard "origin/$DEPLOY_BRANCH"
AFTER_SHA="$(git rev-parse HEAD)"

if [ "$BEFORE_SHA" = "$AFTER_SHA" ]; then
  log "Already at $AFTER_SHA — rebuilding anyway (image may be stale)"
else
  log "Updated $BEFORE_SHA → $AFTER_SHA"
fi

# ── Build & restart ─────────────────────────────────────────────────────────
log "Building image for service '$COMPOSE_SERVICE'"
docker compose build "$COMPOSE_SERVICE"

log "Restarting service '$COMPOSE_SERVICE' (detached)"
docker compose up -d "$COMPOSE_SERVICE"

# ── Ensure Cloudflare tunnel is running (idempotent) ───────────────────────
CLOUDFLARED_LOG="${CLOUDFLARED_LOG:-$APP_DIR/logs/cloudflared.log}"
CLOUDFLARED_PID_FILE="${CLOUDFLARED_PID_FILE:-$APP_DIR/logs/cloudflared.pid}"
mkdir -p "$(dirname "$CLOUDFLARED_LOG")"

if pgrep -fq 'cloudflared.*tunnel.*run'; then
  log "Cloudflare tunnel already running (pid=$(pgrep -f 'cloudflared.*tunnel.*run' | head -1))"
else
  if ! command -v cloudflared >/dev/null 2>&1; then
    log "WARN: cloudflared is not installed on PATH — skipping tunnel start"
  else
    # Read TUNNEL_TOKEN from the env file (not from the shell environment,
    # so we don't require it to be exported for the runner).
    TUNNEL_TOKEN="$(grep -E '^[[:space:]]*TUNNEL_TOKEN[[:space:]]*=' "$APP_DIR/env" \
                    | head -1 \
                    | sed -E 's/^[[:space:]]*TUNNEL_TOKEN[[:space:]]*=[[:space:]]*//; s/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/' \
                    || true)"
    if [ -z "${TUNNEL_TOKEN:-}" ]; then
      log "WARN: TUNNEL_TOKEN not found in $APP_DIR/env — skipping tunnel start"
    else
      log "Starting Cloudflare tunnel (logs: $CLOUDFLARED_LOG)"
      nohup cloudflared tunnel --no-autoupdate run --token "$TUNNEL_TOKEN" \
        >>"$CLOUDFLARED_LOG" 2>&1 </dev/null &
      echo $! >"$CLOUDFLARED_PID_FILE"
      # Give it a moment to fail fast if the token is bad
      sleep 2
      if pgrep -fq 'cloudflared.*tunnel.*run'; then
        log "Cloudflare tunnel started (pid=$(cat "$CLOUDFLARED_PID_FILE"))"
      else
        log "WARN: cloudflared exited immediately — check $CLOUDFLARED_LOG"
      fi
    fi
    unset TUNNEL_TOKEN
  fi
fi

# ── Ensure observability stack is running (idempotent no-op if healthy) ─────
OBSERVABILITY_SERVICES=(loki promtail grafana)
RUNNING_SERVICES="$(docker compose ps --services --status running 2>/dev/null || true)"
missing_services=()
for svc in "${OBSERVABILITY_SERVICES[@]}"; do
  if ! grep -qx "$svc" <<<"$RUNNING_SERVICES"; then
    missing_services+=("$svc")
  fi
done
if [ "${#missing_services[@]}" -gt 0 ]; then
  log "Starting observability services: ${missing_services[*]}"
  docker compose up -d "${missing_services[@]}" || log "WARN: failed to start observability services (non-fatal)"
else
  log "Observability services already running: ${OBSERVABILITY_SERVICES[*]}"
fi

# ── Housekeeping ────────────────────────────────────────────────────────────
log "Pruning dangling images"
docker image prune -f >/dev/null || true

# ── Health check ────────────────────────────────────────────────────────────
log "Waiting up to ${HEALTH_TIMEOUT_SECONDS}s for $HEALTH_URL"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsS --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
    log "Healthy ✓ commit=$AFTER_SHA"
    exit 0
  fi
  sleep 2
done

log "Health check FAILED — dumping recent container logs:"
docker compose logs --tail 120 "$COMPOSE_SERVICE" || true
exit 1
