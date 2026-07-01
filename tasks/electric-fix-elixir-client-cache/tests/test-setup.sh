#!/usr/bin/env bash
# Sourced before the verifier runs.
#
# The Elixir runner (src/resources/scripts/runners/elixir.py) copies
# verify_test.exs into
# packages/elixir-client/test/__verification__/verify_test.exs and
# runs `mix test test/__verification__/verify_test.exs` from
# packages/elixir-client/. The image already pre-fetched and
# pre-compiled `mix deps` plus the project; this script only re-warms
# what an agent's edits might have invalidated.
set -euo pipefail

cd /repo/electric/packages/elixir-client

export MIX_ENV=test

# Some agent diffs touch lib/, so re-fetch deps and recompile the
# project test build. Both operations are no-ops on a hot cache and
# add only a few seconds in the worst case.
mix deps.get >/dev/null 2>&1 || true
mix deps.compile >/dev/null 2>&1 || true
mix compile

# Verify the test infrastructure is reachable from the verification
# injection point. If this fails the runner produces a useful
# early-failure message.
mix run -e 'IO.puts("compile-ok: #{inspect(Code.ensure_loaded?(Bypass))}")'
