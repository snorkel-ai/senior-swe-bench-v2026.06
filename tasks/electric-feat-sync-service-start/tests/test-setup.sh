#!/usr/bin/env bash
# Behavioural verification lives in validation_spec.toml stories (driven
# by mix-test against a real PostgreSQL). The smoke phase still needs the
# source to compile cleanly, and it needs Postgres reachable from the
# sync-service so any verifier that actually runs `mix test` can succeed.
#
# This script is `source`d by the runner, so traps and background PIDs
# persist into the verifier process.

set -euo pipefail

# ---------- PostgreSQL setup ----------
# Start PostgreSQL if it is not already running. The image has PostgreSQL 15
# installed and configured to listen on port 54321 with logical replication
# enabled and trust auth for local connections.
if ! pg_isready -h 127.0.0.1 -p 54321 -U postgres -q 2>/dev/null; then
    pg_ctlcluster 15 main start
fi

# Wait until PostgreSQL is accepting connections on port 54321.
until pg_isready -h 127.0.0.1 -p 54321 -U postgres -q 2>/dev/null; do sleep 0.5; done

# Redirect the query (pooled) URL to port 54321 so both the admin pool and
# the snapshot pool connect to the same PostgreSQL instance. The default
# .env.test points ELECTRIC_QUERY_DATABASE_URL to port 65432 (PgBouncer)
# which is not present in the test environment.
sed -i 's|ELECTRIC_QUERY_DATABASE_URL=.*|ELECTRIC_QUERY_DATABASE_URL=postgresql://postgres:password@localhost:54321/postgres?sslmode=disable|' \
    /repo/electric/packages/sync-service/.env.test

# ---------- Elixir compilation ----------
cd /repo/electric/packages/sync-service

# Ensure deps are present (the image pre-compiled them at build time,
# but any agent edits to mix.exs would invalidate that — re-fetch is
# cheap if nothing changed).
MIX_ENV=test mix deps.get
MIX_ENV=test mix deps.compile --quiet

# Compile with warnings shown but not fatal. The post-implementation
# code may emit harmless deprecation warnings about restructured
# helpers; those don't block test runs.
MIX_ENV=test mix compile

cd /repo/electric
