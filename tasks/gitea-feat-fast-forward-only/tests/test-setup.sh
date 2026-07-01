#!/usr/bin/env bash
#
# Sourced (not exec'd, so exported env sticks) before the verifier runs. The
# Dockerfile materialised tests/sqlite.ini, pre-warmed the Go module/compile
# cache, and built the main `gitea` binary (needed by git server-side hooks
# during integration-test setup). This restores any of those the agent's edits
# might have removed and pins the env the integration test binary needs.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/gitea}"
cd "$REPO_DIR"

# CGO is mandatory (mattn/go-sqlite3 is gitea's only sqlite driver), and these
# mirror gitea's Makefile defaults. GOTOOLCHAIN=local prevents go from trying to
# download a newer toolchain on a cold environment.
export CGO_ENABLED=1
export GOEXPERIMENT=jsonv2
export GOTOOLCHAIN=local

# The gitea integration harness reads its config from this path.
export GITEA_TEST_CONF="${GITEA_TEST_CONF:-tests/sqlite.ini}"

# Make `go` and any built tools reachable even if the harness scrubbed PATH.
case ":$PATH:" in
    *":/usr/local/go/bin:"*) ;;
    *) export PATH=/usr/local/go/bin:/root/go/bin:$PATH ;;
esac

# Re-materialise tests/sqlite.ini if it's missing (image build creates it; an
# agent `git clean -fdx` would erase it). Both placeholders in the template
# must be substituted, and the unsafe-root key must be present because
# UnsetUnnecessaryEnvVars scrubs the GITEA_* env path before the root-uid check
# reads the INI.
if [[ ! -f tests/sqlite.ini ]]; then
    sed -e 's|{{WORK_PATH}}|/repo/gitea/tests/integration/gitea-integration-sqlite|g' \
        -e 's|{{TEST_LOGGER}}|test,file|g' \
        tests/sqlite.ini.tmpl > tests/sqlite.ini
fi
if ! grep -q '^I_AM_BEING_UNSAFE_RUNNING_AS_ROOT' tests/sqlite.ini; then
    sed -i '1a I_AM_BEING_UNSAFE_RUNNING_AS_ROOT = true' tests/sqlite.ini
fi

# Ensure the main gitea binary exists. gitea installs git pre-receive hooks into
# each test repo that shell out to `/repo/gitea/gitea`; without it, repo init
# and branch/file-edit setup steps in the integration test fail. Rebuilding from
# the current tree is cheap on the warm cache and keeps the hook binary
# consistent with the agent's code. (`/gitea` is gitignored, so it never appears
# in the agent's diff.)
if [[ ! -x gitea ]]; then
    go build -tags 'sqlite sqlite_unlock_notify' -o gitea code.gitea.io/gitea \
        >/dev/null 2>&1 || true
fi

# pytest may drive the Python verifier orchestrator; ensure it's importable.
if ! python3 -c 'import pytest' 2>/dev/null; then
    pip install --quiet --break-system-packages pytest 2>/dev/null \
        || pip install --quiet pytest 2>/dev/null \
        || true
fi

echo "test-setup.sh: ready (GITEA_TEST_CONF=$GITEA_TEST_CONF, gitea binary present: $([[ -x gitea ]] && echo yes || echo no))"
