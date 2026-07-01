#!/usr/bin/env bash
# Sourced (not exec'd) by the harbor runner so env vars and any background
# process state stays alive for the verifier.

# `pipefail` is critical here — without it, `pnpm install ... | tail -10`
# masks pnpm failures behind a successful `tail`, which would silently
# skip the network-fallback path on offline-install failure.
set -e
set -o pipefail

REPO_DIR="${REPO_DIR:-/repo/posthog}"
cd "$REPO_DIR"

# ---------------------------------------------------------------- #
# Frontend deps                                                     #
# ---------------------------------------------------------------- #
# pnpm install is heavy (~1.5GB of node_modules) and is intentionally NOT
# baked into the Docker image — the resulting layer pushes the Modal
# environment-start budget over its 1200s ceiling. The Dockerfile pre-
# fetched the bulk of the registry into pnpm's content-addressable store
# via `pnpm fetch`, so the install here is mostly hard-link materialisation.
#
# The offline path can still miss when a workspace dep specifies "latest"
# (e.g. unlayer-types@latest) — pnpm fetch resolves "latest" to a concrete
# version at fetch time but the lockfile may resolve it differently here,
# producing ERR_PNPM_NO_OFFLINE_META. That's why we fall back to a regular
# (online) install on offline failure rather than aborting.

install_frontend_deps() {
    echo "[test-setup] installing frontend dependencies (pnpm)..."
    cd "$REPO_DIR"
    if pnpm install --offline --no-frozen-lockfile 2>&1 | tail -10; then
        echo "[test-setup] offline install succeeded"
        return 0
    fi
    echo "[test-setup] offline install failed (likely missing meta for a 'latest' dep); retrying online..."
    pnpm install --no-frozen-lockfile 2>&1 | tail -20
}

if [ ! -d "$REPO_DIR/frontend/node_modules" ]; then
    install_frontend_deps
fi

# pnpm install may modify pnpm-lock.yaml as an infrastructure side-effect.
# Mark it assume-unchanged so check_repo_integrity() doesn't flag it as a
# dirty-tree contamination.
git update-index --assume-unchanged pnpm-lock.yaml 2>/dev/null || true
git update-index --assume-unchanged frontend/pnpm-lock.yaml 2>/dev/null || true

# Sanity: the verifier needs jest to be runnable from the frontend workspace.
( cd "$REPO_DIR/frontend" && pnpm exec jest --version >/dev/null 2>&1 ) \
    || echo "[test-setup] WARNING: pnpm exec jest not runnable from frontend/"

echo "[test-setup] ready: $REPO_DIR (node=$(node -v 2>/dev/null), pnpm=$(pnpm -v 2>/dev/null))"
