#!/usr/bin/env bash
#
# Validation environment setup for the fast-forward-only-merge-with-signed-
# commits feature. Runs after tests/test-setup.sh and layers on the bits the
# go-test validation driver needs, defensive against an agent having wiped
# image-time state. The stories drive the in-process integration harness
# (onGiteaRun), which installs server-side git hooks that shell out to the
# `gitea` binary, so that binary must exist. IDEMPOTENT — may be sourced
# multiple times.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/gitea}"

cd "$REPO_DIR"

# Re-export the Go toolchain knobs the go-test driver depends on.
export CGO_ENABLED="${CGO_ENABLED:-1}"
export GOEXPERIMENT="${GOEXPERIMENT:-jsonv2}"
export GOTOOLCHAIN="${GOTOOLCHAIN:-local}"
export GOCACHE="${GOCACHE:-/root/.cache/go-build}"
export GOMODCACHE="${GOMODCACHE:-/root/go/pkg/mod}"
export GITEA_TEST_CONF="${GITEA_TEST_CONF:-tests/sqlite.ini}"

mkdir -p "$GOCACHE" "$GOMODCACHE"

# Make `go` reachable even if the harness scrubbed PATH.
case ":$PATH:" in
    *":/usr/local/go/bin:"*) ;;
    *) export PATH=/usr/local/go/bin:/root/go/bin:$PATH ;;
esac

# Ensure tests/sqlite.ini exists with the root-unsafe flag (the gitea
# bootstrap reads I_AM_BEING_UNSAFE_RUNNING_AS_ROOT from this file because
# env-var scrubbing happens before the root check). The image build already
# materialised it, but a `git clean -fdx` from CC would wipe it.
if [[ ! -f tests/sqlite.ini ]]; then
    sed -e 's|{{WORK_PATH}}|/repo/gitea/tests/integration/gitea-integration-sqlite|g' \
        -e 's|{{TEST_LOGGER}}|test,file|g' \
        tests/sqlite.ini.tmpl > tests/sqlite.ini
fi
if ! grep -q '^I_AM_BEING_UNSAFE_RUNNING_AS_ROOT' tests/sqlite.ini; then
    sed -i '1a I_AM_BEING_UNSAFE_RUNNING_AS_ROOT = true' tests/sqlite.ini
fi

# The integration harness installs server-side hooks that exec the `gitea`
# binary; repo init / push in test setup fails without it. The image built
# it from the pre-fix tree; rebuild only if missing (e.g. agent ran `git
# clean`). The merge signing logic under test runs in-process inside the
# test binary, so a pre-fix-built helper binary is fine here.
if [[ ! -x "$REPO_DIR/gitea" ]]; then
    go build -tags 'sqlite sqlite_unlock_notify' -o "$REPO_DIR/gitea" code.gitea.io/gitea
fi

# Belt-and-suspenders module fetch; a no-op on the image's warm cache.
go mod download 2>/dev/null || true

echo "validation-setup.sh: ready (CGO_ENABLED=$CGO_ENABLED, tags='sqlite sqlite_unlock_notify', gitea binary present)"
