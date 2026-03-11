#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Ruff lint"
python -m ruff check .

echo "==> Ruff format"
python -m ruff format --check .

echo "==> Mypy"
python -m mypy .

echo "==> Bandit"
python -m bandit -c bandit.yaml -r .

if [[ "${SKIP_PIP_AUDIT:-}" == "1" ]]; then
  echo "==> Pip-audit (skipped; set SKIP_PIP_AUDIT=0 to run)"
else
  echo "==> Pip-audit"
  python -m pip_audit -r requirements.txt -r requirements-dev.txt
fi

echo "==> Pytest (unit)"
pytest -m "not integration"

if [[ "${RUN_INTEGRATION:-}" == "1" ]]; then
  echo "==> Pytest (integration)"
  pytest -m integration
fi

echo "All checks passed."
