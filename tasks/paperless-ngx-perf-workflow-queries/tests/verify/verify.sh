#!/usr/bin/env bash
# Behavioral verifier — runs verify_tests.py via paperless-ngx's uv-managed
# pytest. Named verify.sh so the SHELL runner discovers it; the test file is
# named verify_tests.py (NOT verify.py) so the Python runner does NOT also
# pick it up and double-run the suite.
set -euo pipefail

cd /repo/paperless-ngx

TEST_FILE="/tests/verify/verify_tests.py"

# Run pytest inside paperless's uv-managed venv (created by the Dockerfile via
# `uv sync --group testing --frozen`). pytest is a member of the testing group;
# Django and paperless come via the project package.
exec uv run --frozen --no-sync pytest "$TEST_FILE" -v --tb=short
