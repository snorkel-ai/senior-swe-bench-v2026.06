#!/usr/bin/env bash
# Validation-phase setup: re-fetch/compile sync-service and ensure the
# validation test directory exists.

set -euo pipefail

cd /repo/electric/packages/sync-service

MIX_ENV=test mix deps.get
MIX_ENV=test mix deps.compile --quiet
MIX_ENV=test mix compile

# The mix-test driver writes generated _test.exs files under this dir.
mkdir -p test/__validation__

cd /repo/electric
