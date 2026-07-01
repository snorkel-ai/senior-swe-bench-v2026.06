#!/usr/bin/env bash
# test-setup.sh — environment bootstrap for the verifier.
#
# The verifier resolves its workspace-package imports through published
# package exports, which point at each package's built `dist/`, not raw
# `src/`. So an agent's `src/` edit is invisible until `dist/` is
# regenerated; we rebuild the affected packages below. Turbo's cache keeps
# this fast for packages whose source did not change.
#
# Sourced (not exec'd) by the harbor runner so env vars set here stay live
# for the verifier subprocess.

set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/better-auth}"
export REPO_DIR
cd "$REPO_DIR"

# js.py reads verify.toml via tomllib (Python 3.11+) or tomli (<=3.10). The
# Ubuntu 22.04 base image ships 3.10 with neither, so without tomli the
# verify.toml is silently dropped and the runner falls back to its `npx jest`
# default, which fails because better-auth uses vitest. Install it defensively.
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

# Rebuild every package whose dist/ the agent's edits could affect. The edits
# are scoped to @better-auth/oauth-provider, but it depends on better-auth core
# types, so rebuilding both avoids turbo's ^build missing rippled type changes.
# @better-auth/core must be built explicitly: it exposes the /error subpath used
# by APIError (see repo notes "Common Gotchas").
echo "test-setup.sh: rebuilding affected workspace packages..."
pnpm --filter better-auth \
     --filter @better-auth/oauth-provider \
     --filter @better-auth/core \
     build

echo "test-setup.sh: ready. REPO_DIR=$REPO_DIR vitest=$(node_modules/.bin/vitest --version 2>/dev/null || echo unknown)"
