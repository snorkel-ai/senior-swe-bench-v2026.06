#!/usr/bin/env bash
# Warm the turborepo-fs crate build and ensure the crate's integration tests
# can use turbopath's AbsoluteSystemPath types (required to call the public
# `recursive_copy` API). turbopath is already a regular dependency of
# turborepo-fs; integration tests in tests/ additionally need it as a
# dev-dependency to `use` it directly.
set -euo pipefail

cd /repo/turborepo

CARGO=crates/turborepo-fs/Cargo.toml

# Idempotently add turbopath to [dev-dependencies] of turborepo-fs.
if ! awk '/^\[dev-dependencies\]/{f=1} f' "$CARGO" | grep -q '^turbopath'; then
    sed -i '/^\[dev-dependencies\]/a turbopath = { workspace = true }' "$CARGO"
fi

# Pre-compile the crate plus its test dependencies so the verifier's first
# `cargo test` invocation only has to compile and run the injected test.
cargo test -p turborepo-fs --no-run
