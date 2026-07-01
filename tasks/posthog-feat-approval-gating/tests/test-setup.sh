#!/usr/bin/env bash
# test-setup.sh — environment bootstrap for the verify.py tests.
# Runs inside the task container BEFORE verify.py.
# Brings up PostgreSQL, syncs PostHog's Python deps, runs migrations, and
# exports the env the Django test framework expects.
#
# This script is `source`d (not exec'd) by the harbor verifier runner so
# that env vars and background services stay alive for pytest.

set -e

REPO_DIR="${REPO_DIR:-/repo/posthog}"
cd "$REPO_DIR"

# ---------------------------------------------------------------- #
# 1. PostgreSQL                                                     #
# ---------------------------------------------------------------- #
# Avoid double-starts if test-setup.sh is sourced more than once
# during a verifier run.
if ! pg_isready -h localhost -p 5432 -q 2>/dev/null; then
    service postgresql start || pg_ctlcluster 15 main start

    # Wait for ready
    for _ in $(seq 1 30); do
        if pg_isready -h localhost -p 5432 -q; then break; fi
        sleep 1
    done
fi

# Create the posthog superuser + database (idempotent)
su - postgres -c 'psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='\''posthog'\''" | grep -q 1 || createuser -s posthog' || true
su - postgres -c 'psql -tAc "ALTER USER posthog WITH PASSWORD '\''posthog'\''"' || true
su - postgres -c 'psql -tAc "SELECT 1 FROM pg_database WHERE datname='\''posthog'\''" | grep -q 1 || createdb -O posthog posthog' || true

# Trust local connections
PG_HBA="/etc/postgresql/15/main/pg_hba.conf"
if [ -f "$PG_HBA" ] && ! grep -q "host all all 127.0.0.1/32 trust" "$PG_HBA"; then
    echo "host all all 127.0.0.1/32 trust" >> "$PG_HBA"
    pg_ctlcluster 15 main reload || service postgresql reload || true
fi

# ---------------------------------------------------------------- #
# 2. PostHog Python deps + migrations                               #
# ---------------------------------------------------------------- #
export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog"
export DJANGO_SETTINGS_MODULE="posthog.settings"
export SECRET_KEY="test-secret-key-for-senior-swe-bench"
export TEST=1
export DEBUG=1
export REDIS_URL="redis://localhost:6379"
export CLICKHOUSE_HOST="localhost"
export CLICKHOUSE_SECURE="False"
export CLICKHOUSE_VERIFY="False"
# Disable object-storage so no S3/MinIO mock is needed for verify.py.
export OBJECT_STORAGE_ENABLED="False"

# uv sync once. Uses the frozen lockfile for reproducibility.
if [ ! -d .venv ]; then
    uv sync --frozen
fi

# Test deps that may not be in the frozen lockfile under [tool.uv].dev
uv pip install -q pytest pytest-django

# Run migrations into the local Postgres. --reuse-db keeps things fast on
# subsequent verifier invocations.
uv run python manage.py migrate --noinput --run-syncdb 2>/dev/null || true

# Make uv's venv python the default for verify.py
export PATH="$REPO_DIR/.venv/bin:$PATH"

# ---------------------------------------------------------------- #
# 3. Optional Redis (only spin up if not already running)           #
# ---------------------------------------------------------------- #
if ! redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 || true
fi

# ---------------------------------------------------------------- #
# 4. Frontend deps (for tsc verifier)                               #
# ---------------------------------------------------------------- #
if [ ! -d "$REPO_DIR/frontend/node_modules" ]; then
    echo "[test-setup] installing frontend dependencies..."
    cd "$REPO_DIR/frontend"
    pnpm install --no-frozen-lockfile 2>&1 | tail -5
    cd "$REPO_DIR"
fi

echo "[test-setup] ready: $REPO_DIR (db=posthog, py=$(.venv/bin/python -V 2>/dev/null))"
