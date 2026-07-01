#!/usr/bin/env bash
# Compilation gate: the change must still build coherently across every touched
# package tree. Never imports task-introduced names.
#
# Multi-module Go project: build the api/ module separately from the root
# module. CGO is required for lib/backend/lite (sqlite3), which transitively
# appears in many lib/auth packages.
set -euo pipefail

export GOFLAGS="${GOFLAGS:--mod=mod}"
export CGO_ENABLED="${CGO_ENABLED:-1}"
export GOTOOLCHAIN="${GOTOOLCHAIN:-local}"

# api/ module: the kube cluster / kube server types the feature touches.
cd /repo/teleport/api
go build ./types/...

# root module: every package the four-layer change must keep coherent
# end-to-end — server-side dedup + pagination, the tsh client lister, the
# tsh display command, and the auth listing path that ties them together.
cd /repo/teleport
go build \
    ./lib/services/... \
    ./lib/kube/... \
    ./lib/auth/... \
    ./tool/tsh/...

echo "verify_build.sh: all touched package trees compile."
