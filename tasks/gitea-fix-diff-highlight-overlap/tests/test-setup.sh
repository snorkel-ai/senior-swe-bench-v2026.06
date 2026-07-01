#!/usr/bin/env bash
# All required deps (Go 1.25.7, libsqlite3-dev, prewarmed module + build
# cache, /repo/gitea/tests/sqlite.ini) are baked into the image. Nothing
# to do at verifier time.
set -euo pipefail
echo "[test-setup] no-op for gitea-fix-diff-highlight-overlap"
