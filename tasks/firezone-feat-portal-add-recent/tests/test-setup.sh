#!/bin/bash
# Boot the test environment for the Portal Elixir app. Idempotent. The image
# pre-baked deps, _build, and the firezone_test DB schema; this starts the
# bundled postgresql (not running by default in the container), waits for it,
# exports test-mode env, and lands at the mix project root.

set -euo pipefail

REPO_DIR="/repo/firezone"
APP_DIR="${REPO_DIR}/elixir"

# ── Postgres ─────────────────────────────────────────────────────────────
service postgresql start >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
    if pg_isready -h localhost -U postgres >/dev/null 2>&1; then break; fi
    sleep 0.5
done

# ── Test env ─────────────────────────────────────────────────────────────
export MIX_ENV=test
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
# Phoenix test config inherits config.exs's hostname/user/password defaults
# (localhost/postgres/postgres). pg_hba.conf is set to trust on localhost
# so the password is accepted without challenge.

# ── Land in the mix project root ─────────────────────────────────────────
cd "${APP_DIR}"

# Sanity check — surface env issues at setup time, not mid-test.
test -f mix.exs || { echo "[test-setup] mix.exs missing at ${APP_DIR}"; exit 1; }
test -d deps   || { echo "[test-setup] deps/ missing — image build went wrong"; exit 1; }
test -d _build/test/lib/portal || { echo "[test-setup] _build/test missing — image build went wrong"; exit 1; }
psql -h localhost -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='firezone_test'" \
    | grep -q 1 || { echo "[test-setup] firezone_test DB missing — image build went wrong"; exit 1; }

# Print a one-line breadcrumb so verifier logs show this ran.
echo "[test-setup] PG ready, MIX_ENV=test, cwd=$(pwd)"
