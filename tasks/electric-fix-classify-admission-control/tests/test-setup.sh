#!/usr/bin/env bash
# `source`d (not exec'd) by the harbor verifier runner, so background
# services started here persist into the verifier process.
#
# Brings up:
# - PostgreSQL 15 on localhost:54321 with logical replication enabled
#   and trust auth (configured in the Dockerfile).
# - Re-fetches and recompiles sync-service mix deps + project (no-op on
#   a hot cache — protects against agent edits to mix.exs / lib/).

set -euo pipefail

# 1. PostgreSQL
if ! pg_isready -h localhost -p 54321 -q 2>/dev/null; then
    service postgresql start || pg_ctlcluster 15 main start
    for _ in $(seq 1 30); do
        if pg_isready -h localhost -p 54321 -q; then break; fi
        sleep 1
    done
fi

if ! pg_isready -h localhost -p 54321 -q; then
    echo "ERROR: postgres failed to start on port 54321" >&2
    return 1 2>/dev/null || exit 1
fi

# Idempotent role + password setup. The Dockerfile already did this once
# during the build, but a Modal sandbox cold start may have lost the
# tmpfs-backed cluster state, so re-apply.
su - postgres -c "psql -p 54321 -tAc \"ALTER USER postgres WITH PASSWORD 'password';\"" >/dev/null 2>&1 || true
su - postgres -c "psql -p 54321 -tAc \"SELECT 1 FROM pg_roles WHERE rolname='unprivileged'\" | grep -q 1 \
    || psql -p 54321 -tAc \"CREATE ROLE unprivileged LOGIN PASSWORD 'password' REPLICATION;\"" >/dev/null 2>&1 || true

# 2. Elixir / mix
cd /repo/electric/packages/sync-service

export MIX_ENV=test
export ELECTRIC_TEST_LOG_LEVEL=error
export SKIP_REPATCH_PREWARM=true
export DATABASE_URL="postgresql://postgres:password@localhost:54321/postgres?sslmode=disable"
export ELECTRIC_QUERY_DATABASE_URL="postgresql://postgres:password@localhost:54321/postgres?sslmode=disable"

# Re-fetch and recompile. Both no-op on a hot cache; protect against
# agent edits to mix.exs / lib/.
mix deps.get >/dev/null 2>&1 || true
mix deps.compile >/dev/null 2>&1 || true
mix compile

# Sanity-check that the sync-service test-support modules load.
mix run -e 'IO.puts("compile-ok: #{inspect(Code.ensure_loaded?(Support.ComponentSetup))}")'

cd /repo/electric
