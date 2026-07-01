#!/usr/bin/env bash
# Environment bootstrap for verify.py + validation stories: PostgreSQL,
# Python backend, and pnpm deps for the frontend/ and nodejs/ workspaces.
# Sourced (not exec'd) so env vars persist for the verifier.
set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/posthog}"
cd "$REPO_DIR"

# PostgreSQL (idempotent across re-sourcing).
if ! pg_isready -h localhost -p 5432 -q 2>/dev/null; then
    service postgresql start || pg_ctlcluster 15 main start
    for _ in $(seq 1 30); do
        if pg_isready -h localhost -p 5432 -q; then break; fi
        sleep 1
    done
fi

# Idempotent role + db.
su - postgres -c 'psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='\''posthog'\''" | grep -q 1 || createuser -s posthog' || true
su - postgres -c 'psql -tAc "ALTER USER posthog WITH PASSWORD '\''posthog'\''"' || true
su - postgres -c 'psql -tAc "SELECT 1 FROM pg_database WHERE datname='\''posthog'\''" | grep -q 1 || createdb -O posthog posthog' || true

# Trust local connections.
PG_HBA="/etc/postgresql/15/main/pg_hba.conf"
if [ -f "$PG_HBA" ] && ! grep -q "host all all 127.0.0.1/32 trust" "$PG_HBA"; then
    echo "host all all 127.0.0.1/32 trust" >> "$PG_HBA"
    pg_ctlcluster 15 main reload || service postgresql reload || true
fi

# PostHog Python deps + Django migrations.
export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog"
export DJANGO_SETTINGS_MODULE="posthog.settings"
export SECRET_KEY="test-secret-key-for-senior-swe-bench"
export TEST=1
export DEBUG=1
export REDIS_URL="redis://localhost:6379"
export CLICKHOUSE_HOST="localhost"
export CLICKHOUSE_SECURE="False"
export CLICKHOUSE_VERIFY="False"
# Disable object-storage so no S3/MinIO mock is needed.
export OBJECT_STORAGE_ENABLED="False"

if [ ! -d .venv ]; then
    uv sync --frozen
fi

# Test deps not in the frozen lockfile under [tool.uv].dev.
uv pip install -q pytest pytest-django

uv run python manage.py migrate --noinput --run-syncdb 2>/dev/null || true

export PATH="$REPO_DIR/.venv/bin:$PATH"

# Redis.
if ! redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 || true
fi

# Frontend + nodejs pnpm deps. Offline install from the prefetched store,
# falling back to online when a "latest" dep can't resolve offline.
install_workspace_deps() {
    local workspace_dir="$1"
    [ -d "$workspace_dir" ] || return 0
    [ -d "$workspace_dir/node_modules" ] && return 0
    echo "[test-setup] installing pnpm deps in $workspace_dir..."
    cd "$workspace_dir"
    if pnpm install --offline --no-frozen-lockfile 2>&1 | tail -10; then
        echo "[test-setup] offline install succeeded ($workspace_dir)"
        cd "$REPO_DIR"
        return 0
    fi
    echo "[test-setup] offline install failed in $workspace_dir; retrying online..."
    pnpm install --no-frozen-lockfile 2>&1 | tail -20
    cd "$REPO_DIR"
}

install_workspace_deps "$REPO_DIR/frontend"
install_workspace_deps "$REPO_DIR/nodejs"

# pnpm may modify the lockfiles; mark assume-unchanged so the integrity
# check doesn't flag a dirty tree.
git update-index --assume-unchanged pnpm-lock.yaml 2>/dev/null || true
git update-index --assume-unchanged frontend/pnpm-lock.yaml 2>/dev/null || true
git update-index --assume-unchanged nodejs/pnpm-lock.yaml 2>/dev/null || true

# Sanity: jest must be runnable from each workspace.
( cd "$REPO_DIR/frontend" && pnpm exec jest --version >/dev/null 2>&1 ) \
    || echo "[test-setup] WARNING: pnpm exec jest not runnable from frontend/"
( cd "$REPO_DIR/nodejs" && pnpm exec jest --version >/dev/null 2>&1 ) \
    || echo "[test-setup] WARNING: pnpm exec jest not runnable from nodejs/"

echo "[test-setup] ready: $REPO_DIR (db=posthog, py=$(.venv/bin/python -V 2>/dev/null), node=$(node -v 2>/dev/null), pnpm=$(pnpm -v 2>/dev/null))"
