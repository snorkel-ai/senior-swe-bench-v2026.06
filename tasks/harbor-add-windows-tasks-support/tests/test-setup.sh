#!/usr/bin/env bash
# test-setup.sh — environment bootstrap for the verifier.
# Sourced (not exec'd) by the harbor verifier runner so env vars stay
# alive for whatever the reward stage runs. Idempotent.
#
# The heavy lifting (editable install of the `harbor` package) belongs in
# the reward stage's validation-setup.sh; this script only sanity-checks
# the toolchain and the repo are on disk.

set -e

REPO_DIR="${REPO_DIR:-/repo/harbor}"
cd "$REPO_DIR"

# Toolchain sanity.
python --version
uv --version

# The repo must still be present and on the pre-fix baseline commit.
git rev-parse HEAD >/dev/null

echo "[test-setup] ready: $REPO_DIR (python=$(python --version 2>&1), uv=$(uv --version 2>&1))"
