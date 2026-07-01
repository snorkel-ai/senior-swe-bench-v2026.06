#!/usr/bin/env bash
# Pre-build the turbo binary so the verifier can locate it and skip the cold compile.
set -euo pipefail

cd /repo/turborepo

export CARGO_INCREMENTAL=1

cargo build -p turbo

# Pre-cache the pnpm version the fixture declares so any incidental corepack
# invocation stays offline. || true: corepack exits non-zero when offline mode
# rejects a re-download even though the cache already has the version.
corepack prepare pnpm@7.33.0 --activate || true

ls -la target/debug/turbo
