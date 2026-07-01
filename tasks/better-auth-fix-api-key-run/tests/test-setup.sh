#!/usr/bin/env bash
# Environment bootstrap for the verifier (vitest, driven through the
# workspace root config). Sourced (not exec'd) by the harbor runner so
# env vars set here stay live for the verifier subprocess.

set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/better-auth}"
export REPO_DIR
cd "$REPO_DIR"

# Verifier-runner dep: the harbor runner shells out to run_verify.py +
# runners/js.py to invoke vitest. js.py reads the per-task verify.toml
# via Python's tomllib (3.11+) or tomli (3.10 and below). The Ubuntu
# 22.04 base image ships Python 3.10, so without tomli the verify.toml
# is silently dropped, runner="vitest" is lost, and the runner falls
# back to its `npx jest` default — which fails because better-auth uses
# vitest. Install defensively here.
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

# Rebuild the workspace packages the verifier imports (better-auth,
# @better-auth/core) so their dist/ is importable and internally
# consistent. api-key resolves via the test's relative `.` import so its
# edits take effect without a rebuild, but we build it too for
# consistency. Cache hits replay instantly for unchanged packages.
echo "test-setup.sh: rebuilding affected workspace packages..."
pnpm --filter @better-auth/api-key \
     --filter better-auth \
     --filter @better-auth/core \
     build

echo "test-setup.sh: ready. REPO_DIR=$REPO_DIR vitest=$(node_modules/.bin/vitest --version 2>/dev/null || echo unknown)"
