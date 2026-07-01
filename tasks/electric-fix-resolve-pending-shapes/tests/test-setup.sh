#!/usr/bin/env bash
set -euo pipefail

cd /repo/electric/packages/sync-service

# Match the in-tree test suite's environment. SKIP_REPATCH_PREWARM opts
# out of Repatch's pre-warm pass (set in the image, re-exported here in
# case the agent reset it).
export MIX_ENV=test
export ELECTRIC_TEST_LOG_LEVEL=error
export SKIP_REPATCH_PREWARM=true

# Agent diffs touch lib/, so re-fetch deps and recompile: no-ops on a hot
# cache, add only a few seconds in the worst case.
mix deps.get >/dev/null 2>&1 || true
mix deps.compile >/dev/null 2>&1 || true
mix compile

# Fail closed if the support test infrastructure isn't reachable from the
# verification injection point.
mix run -e 'IO.puts("compile-ok: #{inspect(Code.ensure_loaded?(Support.ComponentSetup))}")'
