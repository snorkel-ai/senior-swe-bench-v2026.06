#!/usr/bin/env bash
#
# Validation env setup, run after tests/test-setup.sh. Must be IDEMPOTENT:
# may be sourced more than once (verifier harness, then validator).

set -euo pipefail

REPO_DIR="${REPO_DIR:-/repo/gitea}"
cd "$REPO_DIR"

# Re-export the Go toolchain knobs the go-test driver needs, inheriting
# anything test-setup.sh already set.
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
# Image build already materialised it, but a `git clean -fdx` from CC
# would wipe it.
if [[ ! -f tests/sqlite.ini ]]; then
    sed -e 's|{{WORK_PATH}}|/repo/gitea/tests/integration/gitea-integration-sqlite|g' \
        tests/sqlite.ini.tmpl > tests/sqlite.ini
fi
if ! grep -q '^I_AM_BEING_UNSAFE_RUNNING_AS_ROOT' tests/sqlite.ini; then
    sed -i '1a I_AM_BEING_UNSAFE_RUNNING_AS_ROOT = true' tests/sqlite.ini
fi

# Ensure SQLite temporary/WAL-mode files are gitignored so they do not
# trigger the post-CC integrity check (git diff).  *.db is already in
# .gitignore but the journal/wal/shm siblings use different extensions.
for pat in '*.db-journal' '*.db-wal' '*.db-shm'; do
    if ! grep -qF "$pat" .gitignore 2>/dev/null; then
        echo "$pat" >> .gitignore
    fi
done

# No-op on the intact pre-warmed cache; recovers a wiped one. Tolerate
# failure — `go test` will surface anything actually missing.
go mod download 2>/dev/null || true

echo "validation-setup.sh: ready (CGO_ENABLED=$CGO_ENABLED, tags='sqlite sqlite_unlock_notify')"
