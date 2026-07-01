#!/bin/bash
# Backend-only: no frontend dependencies are required.
#
# Sourced (not exec'd) by the validation runner so env vars and
# background services persist for the validation agent.

set -euo pipefail

cd /repo/posthog
export TEST=1
export DEBUG=1

# ── PostgreSQL setup (matching PostHog CI) ──────────────────────────────
echo "=== Starting PostgreSQL ==="
PG_VERSION=$(pg_lsclusters -h | awk '{print $1}' | head -1)
pg_ctlcluster "$PG_VERSION" main start 2>&1 || true
sleep 2

su - postgres -c "
createuser -s posthog 2>/dev/null || true
psql -c \"ALTER USER posthog PASSWORD 'posthog'\" 2>/dev/null
createdb -O posthog posthog 2>/dev/null || true
createdb -O posthog posthog_persons 2>/dev/null || true
" 2>&1 | tail -3

# Trust localhost so the Django connection just works.
PG_HBA="/etc/postgresql/$PG_VERSION/main/pg_hba.conf"
if [ -f "$PG_HBA" ]; then
    echo "local all all trust" > "$PG_HBA"
    echo "host all all 127.0.0.1/32 trust" >> "$PG_HBA"
    echo "host all all ::1/128 trust" >> "$PG_HBA"
    pg_ctlcluster "$PG_VERSION" main restart 2>&1 || true
    sleep 2
fi

# ── Python backend ──────────────────────────────────────────────────────
echo "=== Installing Python dependencies ==="
command -v uv >/dev/null 2>&1 || pip install uv -q

# PostHog's pyproject.toml pins required-version = "~=0.10.2"; the container
# may ship a newer uv. Remove the constraint so uv sync works with any version.
sed -i '/^\s*required-version\s*=/d' pyproject.toml 2>/dev/null || true

uv sync --frozen 2>&1 | tail -3
uv pip install pytest pytest-django -q 2>&1 | tail -1

export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog"
export SECRET_KEY="test-secret-key-for-senior-swe-bench"
export DJANGO_SETTINGS_MODULE="posthog.settings"
export REDIS_URL="redis://localhost:6379"
# HyperCache uses object-storage (S3/MinIO) as a fallback persistence
# layer; in the validation container we disable it so HyperCache runs
# Redis-only and tests don't hang waiting for a missing MinIO.
export OBJECT_STORAGE_ENABLED="False"

echo "=== Running Django migrations ==="
uv run python manage.py migrate --noinput 2>&1 | tail -5

# ── Redis (cheap if not already running) ────────────────────────────────
if ! redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 || true
fi

export PYTHONWARNINGS="ignore::DeprecationWarning"

echo "=== Validation setup complete ==="
