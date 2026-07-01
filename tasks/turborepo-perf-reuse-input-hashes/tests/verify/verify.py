"""Regression guards: run the existing `turborepo-scm` and
`turborepo-lockfiles` crate-level unit-test suites to catch over-broad fixes.

- Reusing ls-tree OIDs unconditionally without checking status entries would
  silently produce stale hashes for modified or deleted files. The
  pre-existing `package_deps::tests::test_get_package_deps`,
  `git_index_regression_tests::test_modified_tracked_files_detected`, and
  friends regress in that case.
- A lockfile presize formula that undersizes the HashMap and drops entries on
  grow is caught by the pre-existing pnpm parsing tests (`test_pnpm_subgraph`
  etc.).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest


REPO_DIR = Path(f"/repo/{os.environ.get('REPO_NAME', 'turborepo')}")


def _run_cargo_test(package: str, *extra: str, timeout: int = 540) -> subprocess.CompletedProcess:
    """Invoke `cargo test -p {package} --lib` from the workspace root."""
    cmd = ["cargo", "test", "-p", package, "--lib", *extra]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_DIR),
        check=False,
    )


_RESULT_LINE = re.compile(
    r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed",
    re.MULTILINE,
)


def _assert_cargo_lib_tests_pass(package: str, *, hint: str) -> None:
    """Run `cargo test -p {package} --lib` and assert all tests passed.

    Aggregates ALL `test result: …` lines (cargo emits one per test
    binary; the workspace can produce several) and asserts the total
    failure count is zero and the total pass count is positive.
    """
    proc = _run_cargo_test(package)
    combined = proc.stdout + "\n" + proc.stderr

    summaries = _RESULT_LINE.findall(combined)

    assert proc.returncode == 0, (
        f"cargo test -p {package} --lib exited with {proc.returncode}.\n"
        f"{hint}\n"
        f"--- last stdout ---\n{proc.stdout[-2000:]}\n"
        f"--- last stderr ---\n{proc.stderr[-2000:]}"
    )
    assert summaries, (
        f"expected to see 'test result: …' summary lines from cargo test, "
        f"got none.\n--- stdout ---\n{proc.stdout[-2000:]}"
    )

    total_passed = sum(int(p) for _, p, _ in summaries)
    total_failed = sum(int(f) for _, _, f in summaries)

    assert total_failed == 0, (
        f"{total_failed} {package} unit tests FAILED — {hint}\n"
        f"--- last stdout ---\n{proc.stdout[-2000:]}\n"
        f"--- last stderr ---\n{proc.stderr[-2000:]}"
    )
    assert total_passed > 0, (
        f"expected at least one {package} unit test to pass; saw zero. "
        f"Did the test binary fail to compile?\n"
        f"--- last stdout ---\n{proc.stdout[-2000:]}"
    )


# --------------------------------------------------------------------------- #
# Regression checks
# --------------------------------------------------------------------------- #


def test_turborepo_scm_lib_tests_pass() -> None:
    """All `turborepo-scm` unit tests must still pass.

    The crate's test surface covers the trickiest interaction points for an
    input-hashing optimization: tracked-vs-modified-vs-deleted classification
    (`git_index_regression_tests::test_modified_tracked_files_detected`,
    `test_deleted_tracked_files_excluded`), parent-directory glob handling
    (`test_inputs_explicit_include_finds_gitignored_files`,
    `test_turbo_default_plus_include_finds_gitignored_files`), config-file
    inclusion (`package_deps::tests::test_get_package_deps`), and the
    `RepoGitIndex` partition invariants. A naive overcorrection that reuses
    ls-tree OIDs without checking status entries regresses several of them.
    """
    _assert_cargo_lib_tests_pass(
        "turborepo-scm",
        hint=(
            "the agent's input-hashing fix likely broke an invariant the "
            "existing tests already pin down (e.g., reused a stale ls-tree "
            "OID for a modified or deleted file, or mishandled untracked / "
            "parent-directory glob matches)."
        ),
    )


def test_turborepo_lockfiles_lib_tests_pass() -> None:
    """All `turborepo-lockfiles` unit tests must still pass.

    Pnpm parsing + subgraph extraction is exercised by `pnpm::tests::*` and
    parser-roundtrip tests. A lockfile presize optimization is purely an
    allocator hint, so these semantic tests must pass regardless of whether
    presize calls were added; a buggy formula that undersizes the map and
    silently drops entries on grow would fail multiple of them.
    """
    _assert_cargo_lib_tests_pass(
        "turborepo-lockfiles",
        hint=(
            "the agent's lockfile presize fix likely altered pnpm parsing "
            "semantics (e.g., undersized a HashMap that then dropped "
            "entries during grow, or perturbed iteration order in a way "
            "that breaks subgraph extraction)."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--tb=short"]))
