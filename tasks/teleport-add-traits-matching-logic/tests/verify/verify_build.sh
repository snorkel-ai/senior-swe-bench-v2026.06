#!/usr/bin/env bash
# Compilation gate for the trait-aware user-filtering feature.
#
# This is the package-build half of the verifier: it asserts that every
# package the implementation must keep coherent still compiles. It imports no
# task-introduced names, so it is alternative-implementation-safe. The
# behavioural discrimination (traits matcher + widened search) lives in
# verify_test.go, and the end-to-end ListUsers behaviour in the validation
# story.
#
# The repo is a multi-module Go project; the api/ module is built separately
# from the root module. CGO is required for the root-module packages that pull
# in lib/backend/lite (sqlite3).
set -euo pipefail

export GOFLAGS="${GOFLAGS:--mod=mod}"
export CGO_ENABLED="${CGO_ENABLED:-1}"
export GOTOOLCHAIN="${GOTOOLCHAIN:-local}"

# api/ module: the user model (UserFilter.Match + user search) and the slices
# helpers package live here.
cd /repo/teleport/api
go build ./types/... ./utils/...

# root module: the pre-existing wiring that filters candidate users through
# UserFilter.Match (lib/services/local) and the gRPC users service that serves
# ListUsers (lib/auth/users) must stay coherent end-to-end.
cd /repo/teleport
go build ./lib/services/local/... ./lib/auth/users/...
