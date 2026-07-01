#!/usr/bin/env bash
# Refresh deps + compile in sync-service before the verifier runs `mix test`.

set -euo pipefail

cd /repo/electric/packages/sync-service

MIX_ENV=test mix deps.get
MIX_ENV=test mix deps.compile --quiet
MIX_ENV=test mix compile

cd /repo/electric
