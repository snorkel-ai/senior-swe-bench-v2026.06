#!/usr/bin/env bash
# Verifier-side setup: runs before verify.py. The image already installed the
# Rust toolchain, warmed the cargo registry, and pre-checked the affected
# crates; this just puts the toolchain on PATH and ensures pytest is available.
set -euo pipefail

# Re-export Rust toolchain paths — the Modal sandbox starts a fresh
# shell per phase, so envs from the Dockerfile RUN lines aren't
# automatically inherited.
export RUSTUP_HOME="${RUSTUP_HOME:-/usr/local/rustup}"
export CARGO_HOME="${CARGO_HOME:-/usr/local/cargo}"
export PATH="$CARGO_HOME/bin:$PATH"

# Sanity check: the toolchain must be present.
rustc --version >/dev/null 2>&1 || {
    echo "ERROR: rustc not found on PATH (CARGO_HOME=$CARGO_HOME)" >&2
    exit 1
}
cargo --version >/dev/null 2>&1 || {
    echo "ERROR: cargo not found on PATH" >&2
    exit 1
}

# Ensure pytest is available for verify.py. The Docker image already
# pip-installed it; this is a belt-and-suspenders no-op when the cache
# is warm.
python3 -c "import pytest" >/dev/null 2>&1 \
    || pip3 install --break-system-packages -q pytest >/dev/null 2>&1 \
    || true

echo "test-setup: rustc=$(rustc --version), cargo=$(cargo --version | head -1)"
