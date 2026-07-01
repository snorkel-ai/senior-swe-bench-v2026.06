#!/usr/bin/env bash
# validation-setup.sh — environment bootstrap for the validation agent's
# story scripts. Runs (via test.sh) AFTER tests/test-setup.sh and BEFORE the
# validation orchestrator dispatches the story-writing agent. Idempotent.
#
# tests/test-setup.sh already does the heavy lifting for this task's medium
# tier: it starts the pre-provisioned Postgres+vchord cluster, ensures the
# `mich` template database exists, restores vitest if an agent edit erased
# it, exports IMMICH_TEST_POSTGRES_URL, and writes
# server/vitest.config.mjs + server/test/medium/local-globalSetup.ts (the
# non-testcontainers wiring the jest driver auto-resolves). This script is a
# fast guard that re-asserts those invariants and fails loudly if the medium
# harness isn't reachable, so a broken environment surfaces here rather than
# as an opaque per-story failure.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/immich}"
SERVER_DIR="$REPO_DIR/server"
cd "$REPO_DIR"

# Sanity: Node + pnpm are reachable.
node --version
pnpm --version

# Postgres must be up (test-setup.sh starts it). Fail loudly if not.
if ! pg_isready -h 127.0.0.1 -p 5432 -q; then
    echo "[validation-setup] FATAL: postgres is not accepting connections on 127.0.0.1:5432" >&2
    exit 1
fi

# The DB URL the medium globalSetup + getKyselyDB read.
export IMMICH_TEST_POSTGRES_URL="${IMMICH_TEST_POSTGRES_URL:-postgres://postgres:postgres@localhost:5432/mich}"
export PATH="$SERVER_DIR/node_modules/.bin:$PATH"

# vitest must be reachable from the server workspace.
if [ ! -x "$SERVER_DIR/node_modules/.bin/vitest" ]; then
    echo "[validation-setup] re-installing server deps to restore vitest..."
    pnpm install --filter "immich" --ignore-scripts --no-frozen-lockfile 2>&1 | tail -10
fi
( cd "$SERVER_DIR" && pnpm exec vitest --version )

# The medium-tier wiring written by test-setup.sh must exist; re-assert it so
# an agent edit between the two scripts can't leave validation without a
# config or globalSetup.
if [ ! -f "$SERVER_DIR/vitest.config.mjs" ] || [ ! -f "$SERVER_DIR/test/medium/local-globalSetup.ts" ]; then
    echo "[validation-setup] WARN: medium vitest wiring missing — re-running tests/test-setup.sh" >&2
    source /tests/test-setup.sh
fi

# Ensure the directory the jest driver copies validation specs into exists.
mkdir -p "$SERVER_DIR/test/medium/specs/__validation__"

echo "[validation-setup] ready: $REPO_DIR"
echo "[validation-setup]   postgres=$(pg_isready -h 127.0.0.1 -p 5432 -q && echo up || echo down)"
echo "[validation-setup]   IMMICH_TEST_POSTGRES_URL=${IMMICH_TEST_POSTGRES_URL}"
