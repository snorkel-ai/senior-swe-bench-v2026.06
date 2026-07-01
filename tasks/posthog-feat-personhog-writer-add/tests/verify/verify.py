"""Structural verifier for posthog-feat-personhog-writer-add.

Criterion (task.toml, level = structural_verifier, fail_to_pass):
    `crate_builds` — the new `personhog-writer` crate exists and is wired
    into the Rust workspace, so `cargo check -p personhog-writer` succeeds.

This tests through the *build interface* only — it never imports or names
any of the crate's internal modules/types (which a valid alternative may
structure or rename freely). It asserts the single fact that any correct
solution must satisfy: the workspace exposes a buildable member package
named `personhog-writer`.

On the pre-fix / nop tree the crate is absent and is not a workspace
member, so `cargo -p personhog-writer` fails to resolve the package and
this test fails. On the reference example (and any valid implementation) it passes.
"""

import os
import subprocess

RUST_DIR = "/repo/posthog/rust"

# cargo lives at /usr/local/cargo/bin in the image; make sure it's on PATH
# regardless of the shell the verifier runner used.
_ENV = {**os.environ, "PATH": "/usr/local/cargo/bin:" + os.environ.get("PATH", "")}


def test_personhog_writer_compiles():
    """`cargo check -p personhog-writer` returns 0 from the rust workspace."""
    assert os.path.isdir(RUST_DIR), f"rust workspace missing at {RUST_DIR}"

    proc = subprocess.run(
        ["cargo", "check", "-p", "personhog-writer"],
        cwd=RUST_DIR,
        env=_ENV,
        capture_output=True,
        text=True,
        timeout=1400,
    )

    # Surface a useful tail of cargo's output on failure for debugging.
    detail = (proc.stdout[-3000:] + "\n" + proc.stderr[-3000:]).strip()
    assert proc.returncode == 0, (
        "cargo check -p personhog-writer failed "
        f"(exit {proc.returncode}). The crate must exist and be registered "
        "as a workspace member in rust/Cargo.toml.\n\n" + detail
    )
