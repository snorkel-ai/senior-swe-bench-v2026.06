#!/usr/bin/env bash
# test-setup.sh — sourced before verify.py runs.
# Prepares the Django + paperless-ngx environment for behavioral testing.
set -euo pipefail

cd /repo/paperless-ngx

# uv-managed venv on $PATH so pytest-django uses the right interpreter.
export PATH="/repo/paperless-ngx/.venv/bin:${PATH}"

# Repo-notes § 3 env vars; already set by Dockerfile ENV but re-export so
# the verifier process sees them even if invoked via a clean env.
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

# Make sure paperless test deps are present (Dockerfile pre-installs but
# guard against partial caches / bind-mount overlays).
uv sync --group testing --frozen

echo "[test-setup] paperless-ngx test env ready"
