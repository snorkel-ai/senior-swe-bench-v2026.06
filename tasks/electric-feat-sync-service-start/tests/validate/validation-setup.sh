#!/usr/bin/env bash
# Validation-phase setup: ensure the post-implementation source compiles
# under MIX_ENV=test and the validation directory exists.
#
# Runs after the agent's solution has been applied to /repo/electric.
# The image already pre-compiled deps; we just re-fetch / re-compile in
# case the agent edited mix.exs or added new modules.

set -euo pipefail

# ---------- PostgreSQL setup ----------
# Start PostgreSQL if it is not already running (it may already be up from
# the smoke test-setup phase that ran earlier in the same container).
if ! pg_isready -h 127.0.0.1 -p 54321 -U postgres -q 2>/dev/null; then
    pg_ctlcluster 15 main start
fi

# Wait until PostgreSQL is accepting connections on port 54321.
until pg_isready -h 127.0.0.1 -p 54321 -U postgres -q 2>/dev/null; do sleep 0.5; done

# Redirect the query (pooled) URL to port 54321 (no PgBouncer in test env).
sed -i 's|ELECTRIC_QUERY_DATABASE_URL=.*|ELECTRIC_QUERY_DATABASE_URL=postgresql://postgres:password@localhost:54321/postgres?sslmode=disable|' \
    /repo/electric/packages/sync-service/.env.test

# ---------- Elixir compilation ----------
cd /repo/electric/packages/sync-service

MIX_ENV=test mix deps.get
MIX_ENV=test mix deps.compile --quiet
MIX_ENV=test mix compile

# The mix-test driver writes generated _test.exs files under this dir.
mkdir -p test/__validation__

cd /repo/electric
