#!/usr/bin/env bash
# Defensive verifier setup: restore node_modules/vitest if an agent's edits
# invalidated them, and drop an auto-discoverable vitest config.

set -e

REPO_DIR="${REPO_DIR:-/repo/immich}"
cd "$REPO_DIR"

node --version
pnpm --version

if [ ! -d "$REPO_DIR/node_modules/.pnpm" ]; then
    echo "[test-setup] pnpm install (server workspace, no scripts)..."
    pnpm install --filter "immich" --ignore-scripts --no-frozen-lockfile 2>&1 | tail -10
fi

if [ ! -x "$REPO_DIR/server/node_modules/.bin/vitest" ]; then
    echo "[test-setup] re-installing server deps to restore vitest..."
    pnpm install --filter "immich" --ignore-scripts --no-frozen-lockfile 2>&1 | tail -5
fi

# The JS runner invokes `npx vitest run` from server/ without --config, so
# drop a re-export at the auto-discovered path that also includes *.test.ts.
# Without it vitest runs with globals=false and skips the verifier file.
if [ ! -f "$REPO_DIR/server/vitest.config.mjs" ]; then
    cat > "$REPO_DIR/server/vitest.config.mjs" <<'EOF'
import baseConfig from './test/vitest.config.mjs';
import { defineConfig, mergeConfig } from 'vitest/config';

export default mergeConfig(
  baseConfig,
  defineConfig({
    test: {
      include: ['src/**/*.spec.ts', 'src/**/*.test.ts'],
    },
  }),
);
EOF
fi

export PATH="$REPO_DIR/server/node_modules/.bin:$PATH"

echo "[test-setup] ready: $REPO_DIR (node=$(node --version), pnpm=$(pnpm --version))"
