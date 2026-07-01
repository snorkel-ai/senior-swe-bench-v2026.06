#!/usr/bin/env bash
# validation-setup.sh — prepares the env the pytest validation stories need.
#
# This task is BACKEND ONLY (no frontend stories), so we only need
# paperless-ngx's uv-managed Python venv (Django, DRF, guardian,
# factory_boy, pytest-django, etc.). The Dockerfile already runs
# `uv sync --group testing --frozen`, so the sync below is a fast no-op.
set -euo pipefail

cd /repo/paperless-ngx

# Source test-setup.sh first to inherit the PAPERLESS_* env vars and the
# venv-on-PATH state used by the verifier.
if [ -f /tests/test-setup.sh ]; then
    # shellcheck disable=SC1091
    source /tests/test-setup.sh
fi

# Backend Python deps — paperless's testing group (pytest, pytest-django,
# factory_boy, DRF testing tools, etc.). Idempotent.
uv sync --group testing --frozen

# Sanity: confirm Django imports + sets up via the venv interpreter, and
# that the harness module is importable from /tests/validate.
uv run --frozen --no-sync python -c "import django; django.setup(); import documents.models; print('paperless django import OK')"

echo "[validation-setup] paperless-ngx-refactor-task-system validation env ready"
