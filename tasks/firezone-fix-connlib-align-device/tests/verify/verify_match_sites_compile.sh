#!/usr/bin/env bash
#
# Structural verifier: confirms the new `FailReason` variants are handled at
# every explicit (non-wildcard) match site in the data plane, by checking that
# both crates with such a site still build:
#
#   * `tunnel`        — `src/dns/device_stub_resolver.rs` (servfail bucket)
#   * `client-shared` — `src/eventloop.rs` (no-op bucket)
#
# Both arms enumerate the variants explicitly, so adding variants WITHOUT
# extending these arms is a non-exhaustive-match compile error. Names no
# specific variant, so it accepts any superset that keeps the match exhaustive.
#
# Uses `cargo check` (lib targets only, NOT `--tests`): pulling in `tunnel`'s
# test deps would drag in `firezone-relay`'s eBPF compile chain (nightly
# toolchain + bpf-linker + LLVM), which this image deliberately does not
# install.

set -euo pipefail

cd /repo/firezone/rust

cargo check -p tunnel -p client-shared
