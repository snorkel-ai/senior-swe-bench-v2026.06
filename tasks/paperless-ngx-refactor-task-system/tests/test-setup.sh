#!/usr/bin/env bash
# Sourced before verify.py runs: prepares the Django + paperless-ngx env.
set -euo pipefail

cd /repo/paperless-ngx

# Ensure the uv-managed venv is active in the verifier's $PATH.
export PATH="/repo/paperless-ngx/.venv/bin:${PATH}"

# Re-export PAPERLESS_* in case the verifier is invoked via a clean env.
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-paperless.settings}"
export PAPERLESS_SECRET_KEY="${PAPERLESS_SECRET_KEY:-test-secret-key-benchmark}"
export PAPERLESS_DATA_DIR="${PAPERLESS_DATA_DIR:-/tmp/paperless-data}"
export PAPERLESS_MEDIA_ROOT="${PAPERLESS_MEDIA_ROOT:-/tmp/paperless-media}"
export PAPERLESS_CONSUMPTION_DIR="${PAPERLESS_CONSUMPTION_DIR:-/tmp/paperless-consume}"
export PAPERLESS_REDIS="${PAPERLESS_REDIS:-redis://localhost:6379}"
export PAPERLESS_DISABLE_DBHANDLER="${PAPERLESS_DISABLE_DBHANDLER:-true}"
export PAPERLESS_CACHE_BACKEND="${PAPERLESS_CACHE_BACKEND:-django.core.cache.backends.locmem.LocMemCache}"
export PAPERLESS_CHANNELS_BACKEND="${PAPERLESS_CHANNELS_BACKEND:-channels.layers.InMemoryChannelLayer}"
export PYTHONPATH="${PYTHONPATH:-/repo/paperless-ngx/src}"

# Ensure the testing extras are present (idempotent).
uv sync --group testing --frozen

# Sanity: Django must import.
uv run python -c "import django; django.setup(); import documents.models; print('paperless-ngx test env ready')"
