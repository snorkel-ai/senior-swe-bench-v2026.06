"""Verifier (nop-gate) for turborepo-feat-add-circular-package.

Behavioral verification lives in the validation suite (the new diagnostic is
observable only by running the built `turbo` binary against fixture monorepos).
This verifier is a minimal `cargo check` compile gate over the three touched
crates: the nop case (no edits) is caught only by validation, but an agent edit
that doesn't type-check is caught here before validation wastes time on a doomed
binary build. The three crates own PackageGraph, the boundaries diagnostic
enum/provider trait, and the GraphQL adapter respectively; the adapter's match
is exhaustive, so a missing arm surfaces as a compile error here.

Stays implementation-agnostic: no imports of agent-named modules, no grep for
cycle-finding helpers / the new variant / the SCC algorithm name, and no
`cargo test` (so it is insensitive to test-name presence).
"""

from __future__ import annotations

import os
import subprocess
import textwrap


# Repository directory inside the task container.
REPO_DIR = "/repo/turborepo"

# Crates touched by this task. Listed by their package name (cargo's
# ``-p`` argument), not their on-disk path. This is the pre-existing,
# stable build interface — both names exist in the pre-fix Cargo.toml.
PACKAGES = (
    "turborepo-repository",
    "turborepo-boundaries",
    "turborepo-query",
)

# 10 minutes is generous: a cold workspace check is ~3-5 min, a warm
# run completes in seconds. The Docker image pre-builds the workspace,
# so this should usually take <30s.
CHECK_TIMEOUT_SEC = 600


def test_changed_crates_compile() -> None:
    """The three crates this task modifies must still ``cargo check`` cleanly.

    Runs ``cargo check -p turborepo-boundaries -p turborepo-query
    -p turborepo-repository --message-format=short`` in /repo/turborepo.

    On failure, dumps the last 200 lines of stdout + stderr so the
    failure mode is visible in the verifier log without flooding the
    judge prompt.
    """
    cmd = ["cargo", "check", "--message-format=short"]
    for pkg in PACKAGES:
        cmd.extend(["-p", pkg])

    env = {**os.environ}
    env.setdefault("CARGO_HOME", "/usr/local/cargo")
    env.setdefault("RUSTUP_HOME", "/usr/local/rustup")
    env["PATH"] = f"{env['CARGO_HOME']}/bin:" + env.get("PATH", "")

    result = subprocess.run(
        cmd,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=CHECK_TIMEOUT_SEC,
        env=env,
        check=False,
    )

    if result.returncode == 0:
        return

    def _tail(text: str, n: int = 200) -> str:
        return "\n".join(text.splitlines()[-n:])

    msg = textwrap.dedent(
        f"""\
        cargo check failed (exit {result.returncode}).
        The agent's edits to one or more of {PACKAGES} produced a
        workspace that no longer type-checks. Common causes:
          - Missing match arm after extending an exhaustive enum
          - Missing trait method on an extended trait impl
          - Broken Cargo.toml feature gating
          - Borrow-checker or lifetime mismatches in the new code

        --- cargo check stdout (last 200 lines) ---
        {_tail(result.stdout)}

        --- cargo check stderr (last 200 lines) ---
        {_tail(result.stderr)}
        """
    )
    raise AssertionError(msg)
