#!/usr/bin/env bash
# Environment bootstrap for the verifier. Sourced (not exec'd) so env
# vars set here stay live for the verifier subprocess.

set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/better-auth}"
export REPO_DIR
cd "$REPO_DIR"

# js.py reads verify.toml via tomllib (Python 3.11+) or tomli (3.10).
# The Ubuntu 22.04 base image ships 3.10 with neither, so verify.toml
# is silently dropped and the runner falls back to its jest default,
# which fails on a vitest repo. Install tomli defensively.
if ! python3 -c "import tomllib" >/dev/null 2>&1 && \
   ! python3 -c "import tomli"   >/dev/null 2>&1; then
    python3 -m pip install --quiet tomli >/dev/null 2>&1 \
        || python3 -m pip install --break-system-packages --quiet tomli >/dev/null 2>&1 \
        || true
fi

# vitest must be resolvable from the workspace root; a miss means a
# corrupted node_modules. Reinstall now instead of failing later with
# a confusing "command not found".
if ! [ -x node_modules/.bin/vitest ]; then
    echo "test-setup.sh: vitest binary missing from node_modules/.bin/. " \
         "The image build is corrupt — re-running pnpm install." >&2
    pnpm install --frozen-lockfile --ignore-scripts
fi

# The verifier reads source files directly via relative imports, so no
# dist/ regen is needed for the agent's edit to be visible. Rebuild
# defensively in case an indirect import travels through a workspace
# package (e.g. @better-auth/core/api) whose dist/ would lag a source
# edit. Turbo's cache makes this a near-noop when nothing changed.
echo "test-setup.sh: rebuilding the better-auth package..."
pnpm --filter better-auth build

echo "test-setup.sh: ready. REPO_DIR=$REPO_DIR vitest=$(node_modules/.bin/vitest --version 2>/dev/null || echo unknown)"
