#!/usr/bin/env bash
# Behavioral verifier — wraps `uv run pytest` so it runs inside paperless-ngx's
# uv-managed venv, where Django, DRF, and paperless's packages are importable.
set -euo pipefail

cd /repo/paperless-ngx

TEST_FILE="/tests/verify/savedview_field_check.py"

exec uv run --frozen --no-sync pytest "$TEST_FILE" -v --tb=short
