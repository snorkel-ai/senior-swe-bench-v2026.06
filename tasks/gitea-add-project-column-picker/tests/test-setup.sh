#!/usr/bin/env bash
#
# test-setup.sh — sourced by the verifier harness BEFORE go test runs.
#
# The Dockerfile already pre-warmed the module + integration-test build
# caches and materialised tests/sqlite.ini with the unsafe-root flag, so
# this script only has to:
#   1. Make sure the env the harness inherits is the right shape.
#   2. Re-materialise tests/sqlite.ini if an agent's edits blew it away.
#
# Sourced (not exec'd) so the environment we export sticks for the runner.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/gitea}"
cd "$REPO_DIR"

# Build tags & cgo are required for the sqlite-backed integration TestMain.
# The Dockerfile exports CGO_ENABLED=1 and GOEXPERIMENT=jsonv2; repeat them
# here so a freshly-spawned shell that didn't inherit the image ENV still
# sees them.
export CGO_ENABLED=1
export GOEXPERIMENT=jsonv2
export GOTOOLCHAIN=local
export GITEA_TEST_CONF=tests/sqlite.ini

# Make `go test` find the toolchain even when the harness scrubs PATH.
case ":$PATH:" in
    *":/usr/local/go/bin:"*) ;;
    *) export PATH=/usr/local/go/bin:/root/go/bin:$PATH ;;
esac

# Re-materialise tests/sqlite.ini if it's missing (defensive: image build
# creates it, but an agent's `git clean -fdx` would erase it). The
# integration TestMain reads I_AM_BEING_UNSAFE_RUNNING_AS_ROOT from the
# config file because SetupGiteaTestEnv scrubs the GITEA_* env-var path
# before reading run-mode config.
if [[ ! -f tests/sqlite.ini ]]; then
    sed -e 's|{{WORK_PATH}}|/repo/gitea/tests/integration/gitea-integration-sqlite|g' \
        tests/sqlite.ini.tmpl > tests/sqlite.ini
fi
if ! grep -q '^I_AM_BEING_UNSAFE_RUNNING_AS_ROOT' tests/sqlite.ini; then
    sed -i '1a I_AM_BEING_UNSAFE_RUNNING_AS_ROOT = true' tests/sqlite.ini
fi

# Install pytest if the harness needs it and it's not there yet.
if ! python3 -c 'import pytest' 2>/dev/null; then
    pip install --quiet --break-system-packages pytest 2>/dev/null \
        || pip install --quiet pytest 2>/dev/null \
        || true
fi

echo "test-setup.sh: ready (CGO_ENABLED=$CGO_ENABLED, tags='sqlite sqlite_unlock_notify')"
