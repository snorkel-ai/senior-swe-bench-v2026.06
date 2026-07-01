# Sourced by the Senior SWE-Bench verifier runner before tests execute.
#
# Inherits the image's Go toolchain (Go 1.25.9) and pre-downloaded module cache
# (root + api modules). The reward harness needs `go build` / `go test` to run
# for packages in both modules, some of which pull in CGO (sqlite via
# lib/backend/lite).
#
# Container setup is therefore minimal — everything heavy lives in the
# Dockerfile. We only export environment so subsequent commands and Go
# toolchain invocations behave deterministically.

set -euo pipefail

cd /repo/teleport

export GOFLAGS="-mod=mod"
export CGO_ENABLED="1"
export GOTOOLCHAIN="local"
export GOCACHE="${GOCACHE:-/root/.cache/go-build}"
export GOMODCACHE="${GOMODCACHE:-/root/go/pkg/mod}"
mkdir -p "$GOCACHE" "$GOMODCACHE"
