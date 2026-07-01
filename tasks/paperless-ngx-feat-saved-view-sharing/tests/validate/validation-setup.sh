#!/usr/bin/env bash
# Extends test-setup.sh with deps the validation stories need: the backend
# pytest stories need paperless's uv-managed venv; the Jest story needs
# pnpm install in src-ui/. Both are fast no-ops when pre-installed.
set -euo pipefail

cd /repo/paperless-ngx

# Source test-setup.sh first to inherit the env vars and venv state.
if [ -f /tests/test-setup.sh ]; then
    # shellcheck disable=SC1091
    source /tests/test-setup.sh
fi

# Backend Python deps (paperless's testing group).
uv sync --group testing --frozen

# Frontend Node deps (Angular + Jest).
if [ -d /repo/paperless-ngx/src-ui ]; then
    cd /repo/paperless-ngx/src-ui
    pnpm install --frozen-lockfile --ignore-scripts
    cd /repo/paperless-ngx
fi

# Sanity: Django imports via the venv interpreter.
uv run --frozen --no-sync python -c "import django; django.setup(); import documents.models; print('paperless django import OK')"

# Sanity: pnpm/jest is reachable.
( cd /repo/paperless-ngx/src-ui && pnpm exec jest --version ) || \
    echo "[validation-setup] WARN: jest --version failed; US6 may not run"

echo "[validation-setup] paperless-ngx validation env ready"
