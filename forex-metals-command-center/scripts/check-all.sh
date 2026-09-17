#!/usr/bin/env bash
# Runs every phase gate locally (macOS/Linux/Git Bash). Stops at the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."
export NEXT_TELEMETRY_DISABLED=1

(
  cd services/api
  uv sync --frozen --python 3.12
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy app
  uv run pytest
)
npm run typecheck
npm test
npm run build
bash scripts/check-guardrails.sh
echo "All checks passed."
