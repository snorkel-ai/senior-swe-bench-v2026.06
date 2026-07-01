#!/usr/bin/env bash
# Test-setup for firezone-fix-connlib-align-device. The image pre-warms the
# cargo cache for the crates the verifier exercises (`client-shared --tests`,
# `tunnel` lib). No services to start, no data to seed.

set -euo pipefail

echo "[test-setup] Rust version:    $(rustc --version 2>&1 | head -1)"
echo "[test-setup] Cargo version:   $(cargo --version 2>&1 | head -1)"
echo "[test-setup] Workspace root:  /repo/firezone/rust"
echo "[test-setup] Setup complete."
