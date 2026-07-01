# Sourced by the verifier runner before tests execute; exports deterministic
# Go environment (toolchain + module cache come from the image).

set -euo pipefail

cd /repo/teleport

export GOFLAGS="-mod=mod"
export CGO_ENABLED="1"
export GOTOOLCHAIN="local"
export GOCACHE="${GOCACHE:-/root/.cache/go-build}"
export GOMODCACHE="${GOMODCACHE:-/root/go/pkg/mod}"
mkdir -p "$GOCACHE" "$GOMODCACHE"
