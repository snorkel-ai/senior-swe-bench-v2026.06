#!/usr/bin/env bash
# validation-setup.sh — environment bootstrap for the validation agent's
# story scripts. Runs (via test.sh) AFTER tests/test-setup.sh and BEFORE the
# validation orchestrator dispatches the story-writing agent. Idempotent.
#
# Re-asserts the unit-tier invariants tests/test-setup.sh established:
# Node/pnpm/vitest reachable from the server workspace, server/vitest.config.mjs
# present (auto-resolved by vitest, no --config), and the validation spec dir
# exists. Fails loudly if the harness isn't reachable, so a broken environment
# surfaces here rather than as an opaque per-story failure.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/immich}"
SERVER_DIR="$REPO_DIR/server"
cd "$REPO_DIR"

# Sanity: Node + pnpm are reachable.
node --version
pnpm --version

export PATH="$SERVER_DIR/node_modules/.bin:$PATH"

# vitest must be reachable from the server workspace.
if [ ! -x "$SERVER_DIR/node_modules/.bin/vitest" ]; then
    echo "[validation-setup] re-installing server deps to restore vitest..."
    pnpm install --filter "immich" --ignore-scripts --no-frozen-lockfile 2>&1 | tail -10
fi
( cd "$SERVER_DIR" && pnpm exec vitest --version )

# The unit-tier vitest wiring written by test-setup.sh must exist; re-assert it
# so an agent edit between the two scripts can't leave validation without a
# config.
if [ ! -f "$SERVER_DIR/vitest.config.mjs" ]; then
    echo "[validation-setup] WARN: server/vitest.config.mjs missing — re-running tests/test-setup.sh" >&2
    source /tests/test-setup.sh
fi

# Ensure the directory the jest driver copies validation specs into exists.
mkdir -p "$SERVER_DIR/src/__validation__"

echo "[validation-setup] ready: $REPO_DIR (node=$(node --version), pnpm=$(pnpm --version))"
echo "[validation-setup]   vitest=$(cd "$SERVER_DIR" && pnpm exec vitest --version 2>/dev/null || echo MISSING)"
