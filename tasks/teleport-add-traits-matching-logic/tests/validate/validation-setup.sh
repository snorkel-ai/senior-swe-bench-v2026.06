#!/usr/bin/env bash
# Validation environment setup for the trait-aware user-filtering feature.
#
# Runs after tests/test-setup.sh has prepared the Go toolchain and before the
# validation agent starts writing its test file. The Docker image already
# pre-downloaded both module caches; we re-export env vars so the go-test
# driver inherits the correct toolchain config.
set -euo pipefail

export GOFLAGS="${GOFLAGS:--mod=mod}"
export CGO_ENABLED="${CGO_ENABLED:-1}"
export GOTOOLCHAIN="${GOTOOLCHAIN:-local}"
export GOCACHE="${GOCACHE:-/root/.cache/go-build}"
export GOMODCACHE="${GOMODCACHE:-/root/go/pkg/mod}"

mkdir -p "$GOCACHE" "$GOMODCACHE"

# Belt-and-suspenders: the image already ran `go mod download` for both
# modules; re-fetch here in case a fresh layer ever loses the cache. Fast when
# warm, recoverable when cold.
( cd /repo/teleport && go mod download ) || true
( cd /repo/teleport/api && go mod download ) || true

echo "validation-setup.sh: Go toolchain ready, both modules' deps cached."
