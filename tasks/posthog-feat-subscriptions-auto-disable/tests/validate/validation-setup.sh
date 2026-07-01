#!/bin/bash
# Validation setup for posthog-feat-subscriptions-auto-disable.
#
# Brings up PostgreSQL + Python venv + Django migrations so the pytest
# stories can drive the live DRF subscriptions endpoints and the Temporal
# delivery workflow/activities, and installs pnpm frontend deps so the single
# jest story can render the pre-existing subscriptions table component.
#
# Sourced (not exec'd) by the validation runner so env vars and background
# services persist for the validation agent.

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

# Apply the persons-database SQL migrations (these tables back the persons_db
# alias). The subscriptions model lookups don't strictly require them, but the
# multi-DB pytest setup routes every alias at the live DB, so we materialise the
# persons schema for parity to avoid spurious lookups failing mid-test.
PERSONS_MIGRATIONS_DIR=/repo/posthog/rust/persons_migrations
if [ -d "$PERSONS_MIGRATIONS_DIR" ]; then
    echo "=== Applying persons-DB migrations to posthog_persons ==="
    for sql_file in $(ls "$PERSONS_MIGRATIONS_DIR"/*.sql 2>/dev/null | sort); do
        echo "  applying $(basename "$sql_file")"
        PGPASSWORD=posthog psql -h 127.0.0.1 -U posthog -d posthog_persons \
            -v ON_ERROR_STOP=1 -f "$sql_file" 2>&1 | tail -3 || true
    done
fi

# ── Python backend ──────────────────────────────────────────────────────
echo "=== Installing Python dependencies ==="
# CRITICAL posthog validation-env provisioning — must survive the SOLVING agent
# corrupting the uv environment. The harbor sandbox exposes an OLD uv (observed
# 0.7.13) ahead of the image's uv on PATH; the agent uses it while solving, and
# 0.7.13 mis-parses PostHog's `[tool.uv] exclude-newer = "7 days"` ("failed to
# parse year in date") and re-resolves /repo/posthog/.venv into a broken ~194-pkg
# set with no usable pkg_resources. So at validation time we:
#   1. force a known-good uv (0.10.12: parses "7 days", satisfies required-version);
#   2. NUKE + rebuild the agent-corrupted .venv from the lockfile (a reconciling
#      `uv sync` cannot repair it — verified);
#   3. install setuptools EXPLICITLY (uv sync does NOT install it; pkg_resources,
#      imported transitively at migrate, comes from setuptools — 80.9.0 still
#      ships it, newer setuptools dropped it);
#   4. run migrate / the smoke guard / the pytest driver via the venv python
#      DIRECTLY, never `uv run` (uv run auto-syncs and re-prunes setuptools).
curl -LsSf https://astral.sh/uv/0.10.12/install.sh | env UV_UNMANAGED_INSTALL=/opt/uvpin sh >/dev/null 2>&1
export PATH="/opt/uvpin:$PATH"
echo "Using uv: $(uv --version 2>&1)"

# Restore canonical deps FIRST: the solving agent's stray-0.7.13 run also corrupts
# uv.lock (re-resolution), so a frozen rebuild from it yields an incompatible dep
# set (e.g. langgraph). Rebuild from the committed baseline lock, not the agent's.
git checkout HEAD -- uv.lock pyproject.toml 2>/dev/null || true
rm -rf /repo/posthog/.venv   # discard the agent's corrupted venv; rebuild clean from the lock
uv sync --frozen 2>&1 | tail -3
uv pip install "setuptools==80.9.0" -q 2>&1 | tail -1
uv pip install pytest pytest-django pytest-asyncio -q 2>&1 | tail -1

export DATABASE_URL="postgres://posthog:posthog@localhost:5432/posthog"
export SECRET_KEY="test-secret-key-for-senior-swe-bench"
export DJANGO_SETTINGS_MODULE="posthog.settings"
export REDIS_URL="redis://localhost:6379"
export OBJECT_STORAGE_ENABLED="False"
export CLICKHOUSE_HOST="localhost"
export CLICKHOUSE_SECURE="False"
export CLICKHOUSE_VERIFY="False"

echo "=== Running Django migrations ==="
# The solving agent may add a migration but forget posthog's django-linear-migrations
# max_migration.txt bookkeeping; regenerate it so a valid migration linearizes and
# migrate applies — the stories then test the FEATURE, not the bookkeeping. A
# genuinely-malformed migration still fails migrate (scored, not masked).
/repo/posthog/.venv/bin/python manage.py create_max_migration_files --recreate 2>&1 | tail -2 || true
/repo/posthog/.venv/bin/python manage.py migrate --noinput 2>&1 | tail -5

# ── Env smoke guard ───────────────────────────────────────────────────────
# Fail LOUDLY (non-zero -> discard) if the validation environment is broken,
# rather than letting every backend pytest story silently score 0.0 on an
# infra fault. Verifies pkg_resources imports, django.setup() works, and the
# migrated schema is actually queryable.
echo "=== Smoke-checking validation env (pkg_resources + django.setup + DB) ==="
if ! /repo/posthog/.venv/bin/python -c "
import pkg_resources  # noqa: F401  (must import; transitive dep of migrate)
import django
django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute('SELECT 1 FROM ee_license LIMIT 0')
print('validation env OK')
"; then
    echo "FATAL: validation environment is broken (pkg_resources / django.setup / migrations). Discarding rather than scoring 0.0." >&2
    exit 1
fi

# ── Redis (cheap if not already running) ────────────────────────────────
if ! redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; then
    redis-server --daemonize yes --bind 127.0.0.1 --port 6379 || true
fi

# ── Frontend (used by the single jest render story) ─────────────────────
# Install pnpm deps so the jest story can render the pre-existing subscriptions
# table via @testing-library/react. --no-frozen-lockfile because the lockfile
# patchedDependencies may not match the installed pnpm version; mark the lockfile
# assume-unchanged so the run_validate.py integrity check (git diff) ignores it.
echo "=== Installing frontend dependencies ==="
cd /repo/posthog/frontend
pnpm install --no-frozen-lockfile 2>&1 | tail -10 || true
cd /repo/posthog
git update-index --assume-unchanged pnpm-lock.yaml 2>/dev/null || true
git update-index --assume-unchanged frontend/pnpm-lock.yaml 2>/dev/null || true

# Non-fatal smoke: confirm @testing-library/react + jsdom boot under the project
# jest config (the render story depends on them).
echo "Smoke-checking @testing-library/react render env..."
SMOKE_FILE="/repo/posthog/frontend/src/scenes/subscriptions/components/__sbrtl_smoke__.test.tsx"
cat > "$SMOKE_FILE" <<'SMOKE'
// @ts-nocheck
import { render, cleanup } from '@testing-library/react'
test('rtl smoke', () => {
    const { container } = render(<div>ok</div>)
    expect(container.textContent).toContain('ok')
    cleanup()
})
SMOKE
( cd /repo/posthog/frontend && pnpm exec jest --testPathPattern "__sbrtl_smoke__" 2>&1 | tail -3 ) \
    || echo "WARNING: @testing-library/react render smoke failed"
rm -f "$SMOKE_FILE"

export PYTHONWARNINGS="ignore::DeprecationWarning"

echo "=== Validation setup complete ==="
