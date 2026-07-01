#!/usr/bin/env bash
# Sourced (not exec'd) by the harbor verifier runner so env vars stay
# alive for pytest.

set -e

REPO_DIR="${REPO_DIR:-/repo/posthog}"
SERVICE_DIR="$REPO_DIR/services/llm-gateway"

cd "$REPO_DIR"

# uv sync is idempotent; the Dockerfile pre-warms the venv via
# `uv sync --frozen --all-groups` so this is normally a fast no-op.
# A Modal sandbox cold start may need to repopulate the venv, so guard
# the sync behind a .venv check.
if [ ! -d "$SERVICE_DIR/.venv" ]; then
    (cd "$SERVICE_DIR" && uv sync --frozen --all-groups)
fi

# verify.py runs via the verify.toml `[pytest] command`
# (`uv run --directory services/llm-gateway pytest`), which resolves the
# service venv and its `pythonpath = ["src"]` so `from llm_gateway...`
# imports work. Do NOT override _SYS_PYTHON to the service venv here: the
# verifier infrastructure (run_verify.py + the judges) runs under
# _SYS_PYTHON and needs the image's standalone python, which carries the
# verifier deps (unidiff, pytest, …) the service venv lacks.

# REPO_NAME is read by /tests/run_verify.py to compute REPO_DIR.
export REPO_NAME=posthog

echo "[test-setup] ready: $SERVICE_DIR (verify.py via 'uv run --directory')"
