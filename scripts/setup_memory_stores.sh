#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-env}"

if [ ! -f "$ENV_FILE" ]; then
  touch "$ENV_FILE"
fi

python - "$ENV_FILE" <<'PY'
from __future__ import annotations

import secrets
import string
import sys
from pathlib import Path

ENV_PATH = Path(sys.argv[1])


def gen_secret(length: int = 40) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def needs_init(value: str | None) -> bool:
    if value is None:
        return True
    cleaned = value.strip().strip('"').strip("'")
    return cleaned == "" or cleaned == "replace_me"


lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
parsed: dict[str, str] = {}
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    parsed[key.strip()] = value.strip()

defaults = {
    "MEMORY_DB_HOST": "127.0.0.1",
    "MEMORY_DB_PORT": "5432",
    "MEMORY_DB_NAME": '"hotel_staff_manager"',
    "MEMORY_DB_USER": '"memory_app"',
    "MEMORY_DB_PASSWORD": f'"{gen_secret()}"',
    "MEMORY_DB_SSLMODE": "disable",
    "MEMORY_DB_SCHEMA": '"memory"',
    "POSTGRES_DB": '"hotel_staff_manager"',
    "POSTGRES_USER": '"memory_app"',
    "POSTGRES_PASSWORD": f'"{gen_secret()}"',
    "MEMORY_REDIS_HOST": "127.0.0.1",
    "MEMORY_REDIS_PORT": "6379",
    "MEMORY_REDIS_DB": "0",
    "MEMORY_REDIS_PASSWORD": f'"{gen_secret()}"',
    "REDIS_PASSWORD": f'"{gen_secret()}"',
}

# Keep the app/bootstrap Postgres credentials aligned.
if not needs_init(parsed.get("MEMORY_DB_USER")):
    defaults["POSTGRES_USER"] = parsed["MEMORY_DB_USER"]
else:
    defaults["POSTGRES_USER"] = defaults["MEMORY_DB_USER"]

if not needs_init(parsed.get("MEMORY_DB_PASSWORD")):
    defaults["POSTGRES_PASSWORD"] = parsed["MEMORY_DB_PASSWORD"]
else:
    defaults["POSTGRES_PASSWORD"] = defaults["MEMORY_DB_PASSWORD"]

if not needs_init(parsed.get("MEMORY_DB_NAME")):
    defaults["POSTGRES_DB"] = parsed["MEMORY_DB_NAME"]
else:
    defaults["POSTGRES_DB"] = defaults["MEMORY_DB_NAME"]

if not needs_init(parsed.get("MEMORY_REDIS_PASSWORD")):
    defaults["REDIS_PASSWORD"] = parsed["MEMORY_REDIS_PASSWORD"]
else:
    defaults["REDIS_PASSWORD"] = defaults["MEMORY_REDIS_PASSWORD"]

updated: list[str] = []
seen: set[str] = set()

for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        updated.append(line)
        continue
    key, _value = line.split("=", 1)
    key = key.strip()
    if key in defaults:
        current = parsed.get(key)
        if needs_init(current):
            updated.append(f"{key}={defaults[key]}")
        else:
            updated.append(line)
        seen.add(key)
    else:
        updated.append(line)

for key, value in defaults.items():
    if key not in seen:
        updated.append(f"{key}={value}")

ENV_PATH.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY

printf 'Memory store credentials initialized in %s\n' "$ENV_FILE"
printf 'Next steps:\n'
printf '  1. Review the generated values in %s\n' "$ENV_FILE"
printf '  2. Start the stores: docker compose up -d memory-postgres memory-redis\n'
printf '  3. Start the app: docker compose up -d app\n'
printf '\n'
printf 'Note: Postgres init scripts run only on the first boot of a fresh Docker volume.\n'
