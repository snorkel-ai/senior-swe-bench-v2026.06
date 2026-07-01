#!/usr/bin/env bash
#
# Sourced (not exec'd) by the verifier harness before go test runs, so
# the env we set persists for pytest / the runner. Re-asserts the Go test
# runtime defensively in case agent edits touched the environment.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/gitea}"
cd "$REPO_DIR"

# Build tags & cgo are required for sqlite-backed unittest.MainTest.
# Needed by a freshly-spawned shell that didn't inherit the image ENV.
export CGO_ENABLED=1
export GOEXPERIMENT=jsonv2
export GOTOOLCHAIN=local
export GITEA_TEST_CONF=tests/sqlite.ini

# Make `go` reachable even if the harness scrubbed PATH.
case ":$PATH:" in
    *":/usr/local/go/bin:"*) ;;
    *) export PATH=/usr/local/go/bin:/root/go/bin:$PATH ;;
esac

# Re-materialise tests/sqlite.ini if it's missing or doesn't carry the
# unsafe-root flag. setting.SetupGiteaTestEnv reads
# I_AM_BEING_UNSAFE_RUNNING_AS_ROOT from the INI file because env-var
# scrubbing happens before the root check; the env-var path won't help.
if [[ ! -f tests/sqlite.ini ]]; then
    sed -e 's|{{WORK_PATH}}|/repo/gitea/tests/integration/gitea-integration-sqlite|g' \
        tests/sqlite.ini.tmpl > tests/sqlite.ini
fi
if ! grep -q '^I_AM_BEING_UNSAFE_RUNNING_AS_ROOT' tests/sqlite.ini; then
    sed -i '1a I_AM_BEING_UNSAFE_RUNNING_AS_ROOT = true' tests/sqlite.ini
fi

# pytest is required by the verifier harness (run_verify.py drives
# Python verifier scripts even when the verifier itself is Go).
if ! python3 -c 'import pytest' 2>/dev/null; then
    pip install --quiet --break-system-packages pytest 2>/dev/null \
        || pip install --quiet pytest 2>/dev/null \
        || true
fi

# No-op on the image's pre-warmed module cache; recovers a wiped layer.
go mod download 2>/dev/null || true

echo "test-setup.sh: ready (CGO_ENABLED=$CGO_ENABLED, tags='sqlite sqlite_unlock_notify', GITEA_TEST_CONF=$GITEA_TEST_CONF)"
