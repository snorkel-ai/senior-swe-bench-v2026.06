#!/bin/bash
# Setup for the prefect-fix-resolve-race-condition verifier.
#
# 1. Installs prefect dev dependencies via uv (the agent may have edited
#    pyproject/uv.lock or removed the venv).
# 2. Starts PostgreSQL (the race condition only manifests reliably under
#    PostgreSQL — SQLite serialises writes at the file level).
# 3. Exports the PG connection URL and Prefect test-mode env vars so
#    fixtures and verifier code talk to PG instead of the default
#    in-memory SQLite.

set -euo pipefail

REPO_DIR="/repo/prefect"

cd "$REPO_DIR"
uv sync --group dev --quiet 2>&1 | tail -5

service postgresql start >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
    if PGPASSWORD=prefect psql -h localhost -U prefect prefect -c 'SELECT 1' \
            >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

export REPO_NAME=prefect
export PREFECT_TESTING_TEST_MODE=1
export PREFECT_TESTING_UNIT_TEST_MODE=1
export PREFECT_SERVER_LOGGING_LEVEL=DEBUG
export PREFECT_API_DATABASE_CONNECTION_URL="postgresql+asyncpg://prefect:prefect@localhost:5432/prefect"
export PREFECT_SERVER_DATABASE_CONNECTION_URL="postgresql+asyncpg://prefect:prefect@localhost:5432/prefect"
