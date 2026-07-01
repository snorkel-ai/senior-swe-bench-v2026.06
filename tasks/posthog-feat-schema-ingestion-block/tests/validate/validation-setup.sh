#!/bin/bash
# Validation setup: PostgreSQL, Python venv + Django migrations, and pnpm
# deps for the frontend/ and nodejs/ workspaces. Sourced (not exec'd) so
# env vars and services persist for the validation agent.
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

# Trust localhost so Django connections work without a password.
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

uv sync --frozen 2>&1 | tail -3
uv pip install pytest pytest-django -q 2>&1 | tail -1

export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog"
export SECRET_KEY="test-secret-key-for-senior-swe-bench"
export DJANGO_SETTINGS_MODULE="posthog.settings"
export REDIS_URL="redis://localhost:6379"
export OBJECT_STORAGE_ENABLED="False"

echo "=== Running Django migrations ==="
uv run python manage.py migrate --noinput 2>&1 | tail -5

# ── Redis (cheap if not already running) ────────────────────────────────
if ! redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 || true
fi

# ── Frontend + nodejs pnpm deps ──────────────────────────────────────────
# Offline install from the prefetched store, falling back to online.
# --no-frozen-lockfile: patchedDependencies refs vary between pnpm versions.
install_workspace_deps() {
    local workspace_dir="$1"
    [ -d "$workspace_dir" ] || return 0
    if [ -d "$workspace_dir/node_modules" ]; then
        echo "[validation-setup] $workspace_dir/node_modules already present"
        return 0
    fi
    echo "[validation-setup] installing pnpm deps in $workspace_dir..."
    cd "$workspace_dir"
    if pnpm install --offline --no-frozen-lockfile 2>&1 | tail -10; then
        echo "[validation-setup] offline install succeeded ($workspace_dir)"
    else
        echo "[validation-setup] offline install failed in $workspace_dir; retrying online..."
        pnpm install --no-frozen-lockfile 2>&1 | tail -20 || true
    fi
    cd /repo/posthog
}

install_workspace_deps "/repo/posthog/frontend"
install_workspace_deps "/repo/posthog/nodejs"

# pnpm may modify the lockfiles; mark assume-unchanged so the integrity
# check doesn't flag a dirty tree.
git update-index --assume-unchanged pnpm-lock.yaml 2>/dev/null || true
git update-index --assume-unchanged frontend/pnpm-lock.yaml 2>/dev/null || true
git update-index --assume-unchanged nodejs/pnpm-lock.yaml 2>/dev/null || true

# Sanity: jest must be runnable from each workspace.
echo "Verifying jest in frontend and nodejs..."
( cd /repo/posthog/frontend && pnpm exec jest --version 2>&1 ) || echo "WARNING: frontend jest not available"
( cd /repo/posthog/nodejs && pnpm exec jest --version 2>&1 ) || echo "WARNING: nodejs jest not available"

export PYTHONWARNINGS="ignore::DeprecationWarning"

echo "=== Validation setup complete ==="
