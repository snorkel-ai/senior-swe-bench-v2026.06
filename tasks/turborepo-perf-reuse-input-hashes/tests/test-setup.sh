#!/usr/bin/env bash
# test-setup.sh — sourced before the verifier runs.
# Pre-builds turborepo-scm's and turborepo-lockfiles' test targets so the
# verifier's cargo invocations don't pay the full cold compile inside
# their own time budgets. The pinned nightly toolchain
# (rust-toolchain.toml at /repo/turborepo) is auto-materialized by rustup
# on first cargo invocation.
set -euo pipefail

cd /repo/turborepo
export CARGO_INCREMENTAL=1

# Pre-warm: build the test binary for turborepo-scm. This populates
# target/ with the compiled deps and the test harness so subsequent
# `cargo test -p turborepo-scm --lib` and `cargo test --test verify_*`
# runs are fast.
cargo test -p turborepo-scm --lib --no-run 2>&1 | tail -3

# Same for turborepo-lockfiles — the regression-test verifier shells out
# to `cargo test -p turborepo-lockfiles --lib`.
cargo test -p turborepo-lockfiles --lib --no-run 2>&1 | tail -3

ls -la target/debug/deps/ 2>/dev/null | head -5 || true
