#!/bin/bash
# Validation setup for posthog-feat-approval-gating.
#
# Brings up PostgreSQL + Python venv + Django migrations so the pytest
# stories can drive the live feature-flag PATCH endpoint, and installs
# pnpm-managed frontend deps so the jest stories can render the
# settings page.
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
# The Dockerfile already installed uv, but install if missing
command -v uv >/dev/null 2>&1 || pip install uv -q

uv sync --frozen 2>&1 | tail -3
uv pip install pytest pytest-django -q 2>&1 | tail -1

export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog"
export SECRET_KEY="test-secret-key-for-senior-swe-bench"
export DJANGO_SETTINGS_MODULE="posthog.settings"
export REDIS_URL="redis://localhost:6379"

echo "=== Running Django migrations ==="
uv run python manage.py migrate --noinput 2>&1 | tail -5

# ── Redis (cheap if not already running) ────────────────────────────────
if ! redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 || true
fi

# ── Frontend (only used by jest stories) ────────────────────────────────
# Use --no-frozen-lockfile because the lockfile patchedDependencies may not
# exactly match the installed pnpm version (pnpm@9 vs the lockfile's origin).
echo "=== Installing frontend dependencies ==="
cd /repo/posthog/frontend
pnpm install --no-frozen-lockfile 2>&1 | tail -10 || true
# pnpm install may update pnpm-lock.yaml as an infrastructure side-effect.
# Mark it assume-unchanged so the integrity check (git diff) doesn't flag it.
cd /repo/posthog
git update-index --assume-unchanged pnpm-lock.yaml 2>/dev/null || true
git update-index --assume-unchanged frontend/pnpm-lock.yaml 2>/dev/null || true

echo "Verifying frontend jest..."
pnpm exec jest --version 2>&1 || echo "WARNING: frontend jest not available"
cd /repo/posthog

export PYTHONWARNINGS="ignore::DeprecationWarning"

echo "=== Validation setup complete ==="
