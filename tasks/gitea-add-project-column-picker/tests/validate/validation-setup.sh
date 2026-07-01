#!/usr/bin/env bash
#
# Validation environment setup for the project-column-picker feature task.
#
# Runs after `tests/test-setup.sh` (which already prepared the gitea Go
# test runtime — sqlite.ini, build tags, env vars). This script layers on
# the bits the go-test validation driver needs to drive the integration
# package (a full in-memory server + sqlite + YAML fixtures):
#
#   - CGO_ENABLED / GOEXPERIMENT / GOTOOLCHAIN exported so `go test` picks
#     the bundled 1.26.2 toolchain and links mattn/go-sqlite3 on a cold
#     shell.
#   - GOCACHE / GOMODCACHE created up-front in case the image's pre-warmed
#     cache was wiped by the agent.
#   - GITEA_TEST_CONF + tests/sqlite.ini regen (with the
#     I_AM_BEING_UNSAFE_RUNNING_AS_ROOT flag) if the agent ran
#     `git clean -fdx`.
#   - sqlite WAL/journal sibling files gitignored so they don't trip the
#     post-CC `git diff` integrity check.
#   - A defensive `go mod download` (no-op on a warm cache).
#
# This script must be IDEMPOTENT — it may be sourced multiple times.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/gitea}"
cd "$REPO_DIR"

# Re-export the Go toolchain knobs the go-test driver needs.
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

# Ensure tests/sqlite.ini exists. setting.SetupGiteaTestEnv reads
# I_AM_BEING_UNSAFE_RUNNING_AS_ROOT from this file because env-var
# scrubbing happens before the root check; the env-var path won't help.
# The image build already materialised it, but a `git clean -fdx` from CC
# would wipe it.
if [[ ! -f tests/sqlite.ini ]]; then
    sed -e 's|{{WORK_PATH}}|/repo/gitea/tests/integration/gitea-integration-sqlite|g' \
        tests/sqlite.ini.tmpl > tests/sqlite.ini
fi
if ! grep -q '^I_AM_BEING_UNSAFE_RUNNING_AS_ROOT' tests/sqlite.ini; then
    sed -i '1a I_AM_BEING_UNSAFE_RUNNING_AS_ROOT = true' tests/sqlite.ini
fi

# Ensure SQLite temporary/WAL-mode files are gitignored so they do not
# trigger the post-CC integrity check (git diff). *.db is already in
# .gitignore but the journal/wal/shm siblings use different extensions.
for pat in '*.db-journal' '*.db-wal' '*.db-shm'; do
    if ! grep -qF "$pat" .gitignore 2>/dev/null; then
        echo "$pat" >> .gitignore
    fi
done

# Belt-and-suspenders module fetch. No-op on a warm cache; recovers a
# wiped layer in seconds behind the CDN. Tolerate failure — `go test`
# surfaces anything actually missing.
go mod download 2>/dev/null || true

echo "validation-setup.sh: ready (CGO_ENABLED=$CGO_ENABLED, tags='sqlite sqlite_unlock_notify')"
