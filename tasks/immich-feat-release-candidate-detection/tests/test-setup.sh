#!/usr/bin/env bash
# test-setup.sh — sourced/run before the verifier (and before validation).
#
# Runs at the server unit tier (Vitest + `newTestService`, repositories mocked,
# no database/Redis/live server). Guarantees the contract the verifier and
# validation drivers rely on:
#   * `pnpm exec vitest` works from /repo/immich/server
#   * /repo/immich/server/vitest.config.mjs exists (auto-resolved by vitest,
#     no --config passed) and mirrors immich's unit config (swc +
#     tsconfigPaths, globals, TZ=UTC) with an `include` that matches specs
#     placed under src/__verification__/ and src/__validation__/
#   * those two directories exist
#
# Files are (re)written on every run, AFTER test.sh has captured the agent
# diff, so they never pollute the graded patch.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/immich}"
SERVER_DIR="$REPO_DIR/server"

cd "$REPO_DIR"
node --version
pnpm --version

# ---------------------------------------------------------------------------
# 1. Restore Node deps / vitest if an agent's edits invalidated them.
# ---------------------------------------------------------------------------
if [ ! -x "$SERVER_DIR/node_modules/.bin/vitest" ]; then
    echo "[test-setup] re-installing server deps to restore vitest..."
    pnpm install --filter "immich" --ignore-scripts --no-frozen-lockfile 2>&1 | tail -10
fi

export PATH="$SERVER_DIR/node_modules/.bin:$PATH"

# ---------------------------------------------------------------------------
# 2. Write the unit-tier vitest config the JS runner auto-resolves.
#
# The JS runner runs `npx vitest run <spec>` from the server workspace WITHOUT
# --config, so vitest auto-resolves server/vitest.config.mjs. Immich's own unit
# config only includes src/**/*.spec.ts; we broaden `include` to also match the
# injected *.test.ts/*.spec.ts specs, keeping swc + tsconfigPaths so `src/...`
# and `test/...` alias imports resolve. No globalSetup — there is no database.
# ---------------------------------------------------------------------------
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
    include: [
      'src/**/*.spec.ts',
      'src/**/*.test.ts',
      'test/**/*.spec.ts',
      'test/**/*.test.ts',
    ],
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

# ---------------------------------------------------------------------------
# 3. Ensure the dirs the verifier / validation drivers copy specs into exist.
# ---------------------------------------------------------------------------
mkdir -p "$SERVER_DIR/src/__verification__" "$SERVER_DIR/src/__validation__"

echo "[test-setup] ready: $REPO_DIR (node=$(node --version), pnpm=$(pnpm --version))"
echo "[test-setup]   vitest=$(cd "$SERVER_DIR" && pnpm exec vitest --version 2>/dev/null || echo MISSING)"
echo "[test-setup]   wrote server/vitest.config.mjs (unit verifier wiring, no DB)"
