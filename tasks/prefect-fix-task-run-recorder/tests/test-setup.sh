#!/bin/bash
# Setup for the prefect-fix-task-run-recorder verifier: sync deps, export
# Prefect test-mode env vars.
#
# This bug reproduces DETERMINISTICALLY on the default in-memory SQLite
# backend: the natural-key UNIQUE constraint (flow_run_id, task_key,
# dynamic_key) exists in both dialects, so two events that map to the same
# logical task run but carry different ids collide regardless of database
# engine. No PostgreSQL needed.

set -euo pipefail

REPO_DIR="/repo/prefect"

# Agent may have changed deps.
cd "$REPO_DIR"
uv sync --group dev --quiet 2>&1 | tail -5

export REPO_NAME=prefect
export PREFECT_TESTING_TEST_MODE=1
export PREFECT_TESTING_UNIT_TEST_MODE=1
export PREFECT_SERVER_LOGGING_LEVEL=DEBUG
