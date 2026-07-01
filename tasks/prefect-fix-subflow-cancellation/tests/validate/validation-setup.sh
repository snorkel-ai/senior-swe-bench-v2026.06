#!/usr/bin/env bash
# validation-setup.sh — runs once before the validation agent starts, after
# tests/test-setup.sh has synced deps and started the Prefect server.
# Re-exports PREFECT_API_URL, then fails fast if the server is unhealthy or
# the harness module can't import — surfacing a bad state immediately rather
# than mid-story.

set -euo pipefail

REPO_DIR=/repo/prefect
SERVER_HOST=127.0.0.1
SERVER_PORT=4200

cd "$REPO_DIR"

# Idempotent dep sync — fast no-op if test-setup.sh already synced.
uv sync --group dev --quiet 2>&1 | tail -5 || true

export PREFECT_HOME="${PREFECT_HOME:-/tmp/prefect-home}"
mkdir -p "$PREFECT_HOME"
export PREFECT_API_URL="http://${SERVER_HOST}:${SERVER_PORT}/api"

healthcheck() {
    local body
    body=$(curl --silent --max-time 2 "${PREFECT_API_URL}/health" 2>/dev/null) || return 1
    [[ "$body" == "true" ]]
}

if ! healthcheck; then
    echo "[validation-setup] PREFECT server not healthy at ${PREFECT_API_URL}." >&2
    echo "[validation-setup] tests/test-setup.sh should have started it." >&2
    echo "[validation-setup] Last 50 lines of server log:" >&2
    tail -50 /logs/verifier/prefect-server.log 2>/dev/null || echo "  (no log)" >&2
    return 1 2>/dev/null || exit 1
fi
echo "[validation-setup] Prefect server healthy at ${PREFECT_API_URL}"

# Smoke-import the harness so bad imports fail fast, before any story runs.
"$REPO_DIR/.venv/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/tests/validate")
import test_harness  # noqa: F401
import prefect       # noqa: F401
from prefect.client.orchestration import get_client  # noqa: F401
from prefect.states import Cancelling                 # noqa: F401
print("[validation-setup] test_harness + prefect imports OK")
PY

export REPO_NAME=prefect
