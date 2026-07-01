#!/usr/bin/env python3
"""Multi-language verification orchestrator.

Discovers verify files in /tests/verify/, runs each via its native runner,
and writes verifier_results.json in the format run_aggregate.py expects.

Called by test.sh as Stage 1:
  python3 /tests/run_verify.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TESTS_DIR = Path("/tests")
VERIFY_DIR = TESTS_DIR / "verify"
LOGS_DIR = Path("/logs/verifier")
REPO_DIR = Path(f"/repo/{os.environ.get('REPO_NAME', '')}")

# Add /tests to sys.path so ssb_lib (which holds runners/) is importable
sys.path.insert(0, str(TESTS_DIR))


def _has_validation_spec() -> bool:
    """Check if this task uses validation stories as its primary reward."""
    return (TESTS_DIR / "validate" / "validation_spec.toml").exists()


def main() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    if not VERIFY_DIR.exists():
        if _has_validation_spec():
            _write_results(0, 0, True, {})
            print("No verify/ directory — validation-primary task, skipping verifiers.")
            return
        _write_runner_failure({"verify": "no verify/ directory found"})
        return

    from ssb_lib.runners import discover_and_run_all

    config = _load_verify_toml()
    results = discover_and_run_all(VERIFY_DIR, REPO_DIR, LOGS_DIR, config)

    if not results:
        if _has_validation_spec():
            _write_results(0, 0, True, {})
            print("No verify files discovered — validation-primary task, skipping verifiers.")
            return
        _write_runner_failure({"verify": "no verify files discovered"})
        return

    # Persist raw runner output for diagnostics
    for name, result in results.items():
        if result.raw_stdout or result.raw_stderr:
            log_path = LOGS_DIR / f"runner_{name}.log"
            with log_path.open("w") as f:
                if result.raw_stdout:
                    f.write("=== STDOUT ===\n")
                    f.write(result.raw_stdout[-5000:])
                    f.write("\n")
                if result.raw_stderr:
                    f.write("=== STDERR ===\n")
                    f.write(result.raw_stderr[-5000:])
                    f.write("\n")

    # Check for runner execution failures (crashes, missing toolchains)
    runner_errors = {name: r.error for name, r in results.items() if r.error is not None}

    # Merge all test results into a flat dict
    all_tests: dict[str, dict] = {}
    for name, result in results.items():
        all_tests.update(result.to_verifier_format(prefix=name))

    passed = sum(1 for t in all_tests.values() if t["pass"])
    total = len(all_tests)

    if runner_errors:
        # A runner failed to execute — discard results (reward=None)
        _write_runner_failure(runner_errors)
        return

    if total == 0:
        # Runners DISCOVERED verify files (results is non-empty — the no-files
        # case returned earlier) but produced ZERO test results: e.g. pytest
        # collected 0 tests, or a parser matched nothing. That is a broken/empty
        # verifier, NOT a vacuous pass (0 == 0). Invalidate the trial (C2).
        _write_runner_failure({"verify": "verify files discovered but produced 0 test results (broken/empty verifier)"})
        return

    _write_results(passed, total, passed == total, all_tests)


def _load_verify_toml() -> dict:
    """Load optional verify.toml configuration."""
    toml_path = VERIFY_DIR / "verify.toml"
    if not toml_path.exists():
        return {}
    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib  # type: ignore[no-redef]
        return tomllib.loads(toml_path.read_text())
    except Exception:
        return {}


def _write_results(passed: int, total: int, all_pass: bool, tests: dict, error: str | None = None) -> None:
    result: dict = {
        "passed": passed,
        "total": total,
        "all_pass": all_pass,
        "tests": tests,
    }
    if error:
        result["error"] = error
    (LOGS_DIR / "verifier_results.json").write_text(json.dumps(result, indent=2))


def _write_runner_failure(errors: dict[str, str]) -> None:
    """Signal runner execution failure — reward=None.

    Writes empty reward files so harbor records RewardFileEmptyError
    rather than a silent reward=0.
    """
    failure_info = {
        "passed": 0,
        "total": 0,
        "all_pass": False,
        "tests": {},
        "runner_errors": errors,
    }
    (LOGS_DIR / "verifier_results.json").write_text(json.dumps(failure_info, indent=2))
    (LOGS_DIR / "reward.txt").write_text("")
    reward_json = LOGS_DIR / "reward.json"
    if reward_json.exists():
        reward_json.unlink()
    for name, err in errors.items():
        print(f"RUNNER FAILURE [{name}]: {err}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback

        traceback.print_exc(file=sys.stderr)
        # C1: a crash before main() wrote verifier_results.json would otherwise
        # leave the file ABSENT — run_aggregate then maps absent→verifier_ok=True
        # and scores a vacuous pass for investigation tasks (incl. nop). Write a
        # runner-failure marker so the trial is INVALID (reward=None), not a pass.
        try:
            _write_runner_failure({"run_verify": f"uncaught exception: {type(e).__name__}: {e}"})
        except Exception:
            traceback.print_exc(file=sys.stderr)
