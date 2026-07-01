#!/bin/bash
# Bring the Portal Elixir app up for validation. Idempotent re-runs of the
# Postgres/test-DB setup (in case validation runs in a different context than
# test-setup.sh), then an incremental recompile so the validation agent's first
# `mix test` isn't a cold compile.

set -euo pipefail

REPO_DIR="/repo/firezone"
APP_DIR="${REPO_DIR}/elixir"

# ── Postgres (idempotent re-start in case test-setup didn't run) ─────────
service postgresql start >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
    if pg_isready -h localhost -U postgres >/dev/null 2>&1; then break; fi
    sleep 0.5
done

# ── Test env ─────────────────────────────────────────────────────────────
export MIX_ENV=test
export LANG=C.UTF-8
export LC_ALL=C.UTF-8

# ── Land in the mix project root ─────────────────────────────────────────
cd "${APP_DIR}"

# Sanity check — the image pre-baked deps + _build + the test DB schema, but
# the agent's edits may have touched config files that affect how mix
# compiles. Recompile incrementally so the validation agent's first
# `mix test` isn't a cold compile.
mix compile --no-validate-compile-env 2>&1 | tail -5 || true

# Ensure the verification dir exists so the mix-test driver's file copy never
# has to mkdir it under the agent's noisy cwd.
mkdir -p "${APP_DIR}/test/__verification__"

echo "[validation-setup] PG ready, MIX_ENV=test, cwd=$(pwd), compiled."
