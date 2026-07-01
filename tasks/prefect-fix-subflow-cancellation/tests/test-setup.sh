#!/usr/bin/env bash
# test-setup.sh — runs once before the verifier (sourced, not exec'd).
# Syncs deps, starts a Prefect server bound to a known port, waits for its
# healthcheck, and exports the env vars the verifier + validation stories
# rely on.

set -euo pipefail

REPO_DIR=/repo/prefect
SERVER_LOG=/logs/verifier/prefect-server.log
SERVER_PIDFILE=/tmp/prefect-server.pid
SERVER_HOST=127.0.0.1
SERVER_PORT=4200

mkdir -p /logs/verifier

cd "$REPO_DIR"

# Agent may have edited pyproject.toml or added dependencies. Sync the dev
# group so prefect is editable-installed and pytest is available.
# In the pre-baked image this is a fast no-op.
uv sync --group dev --quiet 2>&1 | tail -5 || true

# Use SQLite (default) — the cancellation behaviour does not depend on
# the backing database engine.
export PREFECT_HOME=/tmp/prefect-home
mkdir -p "$PREFECT_HOME"
export PREFECT_API_URL="http://${SERVER_HOST}:${SERVER_PORT}/api"
export PREFECT_SERVER_LOGGING_LEVEL=INFO
export PREFECT_LOGGING_LEVEL=INFO
export PREFECT_SERVER_ANALYTICS_ENABLED=false
export PREFECT_SERVER_API_HOST=0.0.0.0
export PREFECT_SERVER_API_PORT="${SERVER_PORT}"

healthcheck() {
    # Returns 0 if /api/health returns the JSON literal "true".
    local body
    body=$(curl --silent --max-time 2 "http://${SERVER_HOST}:${SERVER_PORT}/api/health" 2>/dev/null) || return 1
    [[ "$body" == "true" ]]
}

if [[ -f "$SERVER_PIDFILE" ]] && kill -0 "$(cat "$SERVER_PIDFILE")" 2>/dev/null && healthcheck; then
    echo "[test-setup] Prefect server already running (pid=$(cat "$SERVER_PIDFILE"))"
else
    echo "[test-setup] Starting Prefect server on ${PREFECT_API_URL} ..."
    rm -f "$SERVER_PIDFILE"
    nohup uv run prefect server start \
        --analytics-off \
        --host 0.0.0.0 \
        --port "${SERVER_PORT}" \
        > "$SERVER_LOG" 2>&1 &
    echo $! > "$SERVER_PIDFILE"

    # Poll the healthcheck up to ~120s
    for _ in $(seq 1 120); do
        if healthcheck; then
            echo "[test-setup] Prefect server is healthy (pid=$(cat "$SERVER_PIDFILE"))"
            break
        fi
        sleep 1
    done

    if ! healthcheck; then
        echo "[test-setup] FAILED to bring server up — last log lines:"
        tail -100 "$SERVER_LOG" || true
        return 1 2>/dev/null || exit 1
    fi
fi

# Make commonly-used env vars available to the verifier subprocess.
export REPO_NAME=prefect
