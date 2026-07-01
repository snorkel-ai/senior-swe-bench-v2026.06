#!/usr/bin/env bash
# Behavioral verifier — runs verify_tests.py via uv-managed pytest.
set -euo pipefail

cd /repo/paperless-ngx

TEST_FILE="/tests/verify/verify_tests.py"

# Run pytest inside paperless's uv-managed venv (created by Dockerfile via
# ``uv sync --group testing --frozen``). pytest is a member of the testing
# group; Django, paperless, and guardian come via the project package.
exec uv run --frozen --no-sync pytest "$TEST_FILE" -v --tb=short
