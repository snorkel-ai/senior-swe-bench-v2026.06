"""Behavioral and regression checks for `gitea-fix-codeql-code-scanning`.

The headline contract changes (NewPagination, SetLinkHeader,
ListUnadoptedRepositories must accept/return int64) are checked by the
two Go test files in this directory: verify_pagination_test.go and
verify_repository_test.go. Those tests fail to compile against pre-fix
code (Go's strict function-value typing) and pass against post-fix.

This file adds two regression gates that are pass-to-pass on both
pre-fix and post-fix code, but BREAK if the agent's fix is incomplete
or inconsistent:

1. `test_gitea_builds_clean` — runs `go build ./...` against the entire
   module. The 60+ call sites of NewPagination/SetLinkHeader/
   ListUnadoptedRepositories must all be aligned with the new
   signatures. If the agent widens the signature but forgets a single
   caller (or vice versa), `go build` fails with a type-mismatch error.
   CGO is disabled — the wide build does not link mattn/go-sqlite3
   (which is only used with the `sqlite` build tag, which we omit
   here).

2. `test_existing_repository_tests_pass` — runs the two pre-existing
   unit tests in services/repository/ that exercise unadoptedRepositories.
   The struct's bookkeeping fields and the test's references must move
   in lockstep through the rename — Go's compiler enforces this. The
   test uses the sqlite build tag because services/repository/
   main_test.go calls unittest.MainTest, which requires the sqlite
   driver and /repo/gitea/tests/sqlite.ini (baked into the image).

Stack/runner notes:
- The image bakes Go 1.26.3 + libsqlite3-dev + a prewarmed Go module
  cache and `go build ./...` cache, so the first invocation of these
  checks runs in seconds rather than minutes.
- GOEXPERIMENT=jsonv2 matches the Makefile default; some json
  marshalling differs without it.
- GITEA_I_AM_BEING_UNSAFE_RUNNING_AS_ROOT is irrelevant here because
  unittest.MainTest reads the `I_AM_BEING_UNSAFE_RUNNING_AS_ROOT`
  flag from /repo/gitea/tests/sqlite.ini, not the env var (the env
  var is only respected during early bootstrap).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_DIR = Path(f"/repo/{os.environ.get('REPO_NAME', 'gitea')}")


def _run_go(args: list[str], *, cgo: bool, timeout: int) -> subprocess.CompletedProcess:
    """Invoke `go {args}` from the gitea repo root.

    cgo=False matches the Makefile default for non-sqlite builds and
    avoids linking mattn/go-sqlite3. cgo=True is required for
    `go test` invocations that compile the sqlite driver via the
    `sqlite` build tag.
    """
    env = dict(os.environ)
    env["CGO_ENABLED"] = "1" if cgo else "0"
    env.setdefault("GOEXPERIMENT", "jsonv2")
    return subprocess.run(
        ["go", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_DIR),
        check=False,
        env=env,
    )


def test_gitea_builds_clean() -> None:
    """`go build ./...` succeeds against the entire module.

    Covers the `gitea_builds_clean` criterion. Pre-fix and post-fix
    both pass when consistent. The discriminating case is "agent
    widened the signature of one of the centre functions but missed
    a caller (or did not update an in-memory-int call site to add
    the int64 cast)" — the build then fails with a type-mismatch
    error pointing at the mismatched caller.
    """
    proc = _run_go(["build", "./..."], cgo=False, timeout=480)

    assert proc.returncode == 0, (
        f"`go build ./...` exited with {proc.returncode}.\n"
        f"--- stderr tail ---\n{proc.stderr[-2500:]}\n"
        f"--- stdout tail ---\n{proc.stdout[-1500:]}"
    )


def test_existing_repository_tests_pass() -> None:
    """The pre-existing services/repository/ tests still pass.

    Covers the `existing_repository_tests_pass` criterion. Two tests
    in adopt_test.go reference fields on the unadoptedRepositories
    struct (pre-fix: `index`, post-fix: `count`). If the agent renames
    the struct field but forgets the test (or vice versa), the test
    binary fails to compile.

    Uses the sqlite build tag so unittest.MainTest can load the
    fixture database from /repo/gitea/tests/sqlite.ini (baked into
    the image).
    """
    proc = _run_go(
        [
            "test",
            "-tags", "sqlite sqlite_unlock_notify",
            "-count=1",
            "-timeout=240s",
            "-run", "^(TestCheckUnadoptedRepositories_Add|TestListUnadoptedRepositories_ListOptions)$",
            "./services/repository/...",
        ],
        cgo=True,
        timeout=360,
    )

    assert proc.returncode == 0, (
        f"`go test ./services/repository/...` exited with "
        f"{proc.returncode}.\n"
        f"--- stdout tail ---\n{proc.stdout[-2000:]}\n"
        f"--- stderr tail ---\n{proc.stderr[-1000:]}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--tb=short"]))
