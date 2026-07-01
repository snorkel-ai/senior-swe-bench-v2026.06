#!/usr/bin/env bash
# test-setup.sh — sourced/run before the verifier.
#
# This task's behavioural verifier runs immich's "medium" test tier
# (server/test/medium/**/*.spec.ts), which exercises the AssetRepository
# against a REAL Postgres database. Upstream those tests spin up Postgres
# via testcontainers (Docker-in-Docker), which isn't available inside the
# task container — so this script starts the locally pre-provisioned
# Postgres 14 + vchord cluster instead, and exposes it via
# IMMICH_TEST_POSTGRES_URL (the exact env var immich's getKyselyDB() reads).
#
# Contract guaranteed to the verifier:
#   * Postgres is accepting connections on 127.0.0.1:5432
#   * superuser `postgres` / password `postgres`, local trust auth
#   * a `mich` template database exists (migrations are run into it by the
#     medium-test globalSetup, then cloned per test)
#   * IMMICH_TEST_POSTGRES_URL=postgres://postgres:postgres@localhost:5432/mich
set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/immich}"
PG_BIN="${PG_BIN:-/usr/lib/postgresql/14/bin}"
PGDATA_TASK="${PGDATA_TASK:-/pgdata}"

# ---------------------------------------------------------------------------
# 1. Start Postgres (vchord preloaded) if it isn't already up.
# ---------------------------------------------------------------------------
start_postgres() {
    if pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
        echo "[test-setup] postgres already accepting connections"
        return
    fi

    install -d -m 0775 -o postgres -g postgres /var/run/postgresql /var/log/postgresql

    echo "[test-setup] starting postgres cluster at ${PGDATA_TASK}"
    gosu postgres "${PG_BIN}/pg_ctl" -D "${PGDATA_TASK}" \
        -l /var/log/postgresql/task.log \
        -o "-c listen_addresses=localhost -c port=5432 -c unix_socket_directories=/var/run/postgresql" \
        -w start

    for _ in $(seq 1 30); do
        if pg_isready -h 127.0.0.1 -p 5432 -q; then
            break
        fi
        sleep 1
    done

    if ! pg_isready -h 127.0.0.1 -p 5432 -q; then
        echo "[test-setup] postgres failed to start; tail of log:" >&2
        tail -40 /var/log/postgresql/task.log >&2 || true
        exit 1
    fi
}

start_postgres

# ---------------------------------------------------------------------------
# 2. Ensure the `mich` template database exists (created at build time, but
#    recreate defensively in case an agent dropped it).
# ---------------------------------------------------------------------------
if ! gosu postgres "${PG_BIN}/psql" -h /var/run/postgresql -p 5432 -U postgres \
        -tAc "SELECT 1 FROM pg_database WHERE datname='mich'" | grep -q 1; then
    echo "[test-setup] creating missing 'mich' template database"
    gosu postgres "${PG_BIN}/createdb" -h /var/run/postgresql -p 5432 -U postgres -O postgres mich
fi

# ---------------------------------------------------------------------------
# 3. Restore Node deps / vitest if an agent's edits invalidated them.
# ---------------------------------------------------------------------------
cd "$REPO_DIR"
node --version
pnpm --version

if [ ! -x "$REPO_DIR/server/node_modules/.bin/vitest" ]; then
    echo "[test-setup] re-installing server deps to restore vitest..."
    pnpm install --filter "immich" --ignore-scripts --no-frozen-lockfile 2>&1 | tail -10
fi

# ---------------------------------------------------------------------------
# 4. Export the DB URL the medium tests read.
# ---------------------------------------------------------------------------
export IMMICH_TEST_POSTGRES_URL="postgres://postgres:postgres@localhost:5432/mich"
export PATH="$REPO_DIR/server/node_modules/.bin:$PATH"

# ---------------------------------------------------------------------------
# 5. Provide the medium-tier vitest wiring for the behavioural verifier.
#
# The harbor JS runner executes `npx vitest run <verifier-file>` from inside
# the server workspace WITHOUT passing --config, so vitest auto-resolves
# `server/vitest.config.mjs`. Immich's own medium config
# (test/vitest.config.medium.mjs) starts Postgres via testcontainers
# (Docker-in-Docker), which is unavailable here. So we write:
#   * a non-testcontainers globalSetup that runs the migration set into the
#     local `mich` template (cloned per test by getKyselyDB), and
#   * a server-root vitest config that mirrors the medium config (swc +
#     tsconfigPaths, globals, TZ=UTC) and points globalSetup at our local
#     variant, with an `include` that matches the injected verifier spec
#     under test/medium/**.
# Written defensively on every run so an agent edit can't leave them stale.
# These files are created AFTER test.sh has already captured the agent diff,
# so they never pollute the graded patch.
# ---------------------------------------------------------------------------
SERVER_DIR="$REPO_DIR/server"

cat > "$SERVER_DIR/test/medium/local-globalSetup.ts" <<'GLOBALSETUP'
// Local-Postgres globalSetup for the behavioural verifier's medium tier.
// Mirrors test/medium/globalSetup.ts but DROPS the testcontainers startup —
// the task container already runs a local Postgres+vchord cluster and
// exports IMMICH_TEST_POSTGRES_URL (pointing at the `mich` template). We
// only need to run the migration set into that template; getKyselyDB then
// clones it per test.
import { Kysely } from 'kysely';
import { ConfigRepository } from 'src/repositories/config.repository';
import { DatabaseRepository } from 'src/repositories/database.repository';
import { LoggingRepository } from 'src/repositories/logging.repository';
import { DB } from 'src/schema';
import { getKyselyConfig } from 'src/utils/database';

const globalSetup = async () => {
  const url = process.env.IMMICH_TEST_POSTGRES_URL;
  if (!url) {
    throw new Error('IMMICH_TEST_POSTGRES_URL is not set — start the local Postgres cluster first');
  }

  const db = new Kysely<DB>(getKyselyConfig({ connectionType: 'url', url }));
  const configRepository = new ConfigRepository();
  const logger = LoggingRepository.create();
  await new DatabaseRepository(db, logger, configRepository).runMigrations();
  await db.destroy();
};

export default globalSetup;
GLOBALSETUP

cat > "$SERVER_DIR/vitest.config.mjs" <<'VITESTCFG'
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import swc from 'unplugin-swc';
import tsconfigPaths from 'vite-tsconfig-paths';
import { defineConfig } from 'vitest/config';

const serverRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  test: {
    name: 'server:verify',
    root: serverRoot,
    globals: true,
    include: ['test/medium/**/*.test.ts', 'test/medium/**/*.spec.ts'],
    globalSetup: ['test/medium/local-globalSetup.ts'],
    hookTimeout: 120_000,
    env: {
      TZ: 'UTC',
    },
    server: {
      deps: {
        fallbackCJS: true,
      },
    },
  },
  plugins: [swc.vite(), tsconfigPaths()],
});
VITESTCFG

mkdir -p "$SERVER_DIR/test/medium/specs/__verification__"
echo "[test-setup]   wrote server/vitest.config.mjs + test/medium/local-globalSetup.ts (medium verifier wiring)"

echo "[test-setup] ready: $REPO_DIR (node=$(node --version), pnpm=$(pnpm --version))"
echo "[test-setup]   postgres=$(pg_isready -h 127.0.0.1 -p 5432 -q && echo up || echo down)"
echo "[test-setup]   IMMICH_TEST_POSTGRES_URL=${IMMICH_TEST_POSTGRES_URL}"
