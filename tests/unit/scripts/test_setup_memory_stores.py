from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "setup_memory_stores.sh"


def _parse_env(env_path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def test_setup_memory_stores_populates_blank_env_file(tmp_path: Path) -> None:
    env_path = tmp_path / "env"
    env_path.write_text("", encoding="utf-8")

    subprocess.run(
        ["bash", str(SCRIPT_PATH), str(env_path)],
        cwd=REPO_ROOT,
        check=True,
    )

    parsed = _parse_env(env_path)

    assert parsed["MEMORY_DB_HOST"] == "127.0.0.1"
    assert parsed["MEMORY_DB_PORT"] == "5432"
    assert parsed["MEMORY_DB_NAME"] == "hotel_staff_manager"
    assert parsed["MEMORY_DB_USER"] == "memory_app"
    assert parsed["MEMORY_DB_PASSWORD"] != "replace_me"
    assert parsed["MEMORY_DB_SCHEMA"] == "memory"
    assert parsed["POSTGRES_DB"] == parsed["MEMORY_DB_NAME"]
    assert parsed["POSTGRES_USER"] == parsed["MEMORY_DB_USER"]
    assert parsed["POSTGRES_PASSWORD"] == parsed["MEMORY_DB_PASSWORD"]
    assert parsed["MEMORY_REDIS_HOST"] == "127.0.0.1"
    assert parsed["MEMORY_REDIS_PORT"] == "6379"
    assert parsed["MEMORY_REDIS_DB"] == "0"
    assert parsed["MEMORY_REDIS_PASSWORD"] != "replace_me"
    assert parsed["REDIS_PASSWORD"] == parsed["MEMORY_REDIS_PASSWORD"]


def test_setup_memory_stores_preserves_existing_credentials(tmp_path: Path) -> None:
    env_path = tmp_path / "env"
    env_path.write_text(
        "\n".join(
            [
                'MEMORY_DB_USER="custom_user"',
                'MEMORY_DB_PASSWORD="custom_password"',
                'MEMORY_DB_NAME="custom_db"',
                'MEMORY_REDIS_PASSWORD="custom_redis_password"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["bash", str(SCRIPT_PATH), str(env_path)],
        cwd=REPO_ROOT,
        check=True,
    )

    parsed = _parse_env(env_path)

    assert parsed["MEMORY_DB_USER"] == "custom_user"
    assert parsed["MEMORY_DB_PASSWORD"] == "custom_password"
    assert parsed["MEMORY_DB_NAME"] == "custom_db"
    assert parsed["POSTGRES_USER"] == "custom_user"
    assert parsed["POSTGRES_PASSWORD"] == "custom_password"
    assert parsed["POSTGRES_DB"] == "custom_db"
    assert parsed["MEMORY_REDIS_PASSWORD"] == "custom_redis_password"
    assert parsed["REDIS_PASSWORD"] == "custom_redis_password"
