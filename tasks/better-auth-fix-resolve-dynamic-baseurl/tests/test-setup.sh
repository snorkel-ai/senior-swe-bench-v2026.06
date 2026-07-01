#!/usr/bin/env bash
# Workspace package imports resolve to each package's published `dist/`
# artifacts, so agent edits under `packages/better-auth/src/` are
# invisible to the verifier until `dist/` is regenerated (see rebuild
# step below).
#
# Sourced (not exec'd) by the harbor runner so any env vars set here
# stay live for the verifier subprocess.

set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/better-auth}"
export REPO_DIR
cd "$REPO_DIR"

# The runner reads verify.toml via tomllib (3.11+) or tomli. The base
# image ships Python 3.10, so without tomli verify.toml is silently
# dropped, runner="vitest" is lost, and the runner falls back to its
# `npx jest` default (which fails: better-auth uses vitest). Install
# defensively here.
if ! python3 -c "import tomllib" >/dev/null 2>&1 && \
   ! python3 -c "import tomli"   >/dev/null 2>&1; then
    python3 -m pip install --quiet tomli >/dev/null 2>&1 \
        || python3 -m pip install --break-system-packages --quiet tomli >/dev/null 2>&1 \
        || true
fi

# Sanity check: vitest must be resolvable from the workspace root.
# A failure here points at a corrupted node_modules — bailing now
# saves time vs. a confusing vitest "command not found" later.
if ! [ -x node_modules/.bin/vitest ]; then
    echo "test-setup.sh: vitest binary missing from node_modules/.bin/. " \
         "The image build is corrupt — re-running pnpm install." >&2
    pnpm install --frozen-lockfile --ignore-scripts
fi

# Rebuild so dist/ reflects agent source edits. @better-auth/core is
# also rebuilt because the verifier imports `createAuthEndpoint` from
# `@better-auth/core/api` and better-auth depends on it; Turbo's
# `^build` chain orders dependencies and cache-hits unchanged packages.
echo "test-setup.sh: rebuilding affected workspace packages..."
pnpm --filter better-auth \
     --filter @better-auth/core \
     build

echo "test-setup.sh: ready. REPO_DIR=$REPO_DIR vitest=$(node_modules/.bin/vitest --version 2>/dev/null || echo unknown)"
