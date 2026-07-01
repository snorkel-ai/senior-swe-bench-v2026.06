#!/usr/bin/env bash
# All required deps (Go 1.26.3, libsqlite3-dev, prewarmed module + build
# cache, /repo/gitea/tests/sqlite.ini) are baked into the image. Nothing
# to do at verifier time.
set -euo pipefail
echo "[test-setup] no-op for gitea-fix-codeql-code-scanning"
