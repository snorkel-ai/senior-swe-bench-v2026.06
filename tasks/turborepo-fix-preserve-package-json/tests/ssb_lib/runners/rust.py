"""Rust test runner — runs verify*_test.rs files via `cargo test`."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import register
from .base import BaseRunner, RunConfig, RunResult, TestResult, fail_closed_on_exit_code


class RustRunConfig(RunConfig):
    """Configuration for running Rust integration tests."""

    crate: str
    workspace_dir: str = ""
    command: str = "cargo test"
    extra_args: str = ""


@register
class RustRunner(BaseRunner):
    name = "rust"

    def discover(self, verify_dir: Path) -> list[Path]:
        return sorted(verify_dir.glob("verify*_test.rs"))

    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> RustRunConfig:
        rust_cfg = verify_toml.get("rust", {})
        return RustRunConfig(
            test_file=test_file,
            repo_dir=repo_dir,
            crate=rust_cfg.get("crate", ""),
            workspace_dir=rust_cfg.get("workspace_dir", ""),
            timeout=rust_cfg.get("timeout", 300),
            env=rust_cfg.get("env", {}),
            command=rust_cfg.get("command", "cargo test"),
            extra_args=rust_cfg.get("extra_args", ""),
        )

    def run_file(self, config: RustRunConfig) -> RunResult:
        return run(config)


def run(config: RustRunConfig) -> RunResult:
    """Inject test file into crate/tests/, run cargo test, parse, cleanup."""
    crate_dir = config.repo_dir / config.crate
    workspace_root = config.repo_dir / config.workspace_dir if config.workspace_dir else config.repo_dir

    tests_dir = crate_dir / "tests"
    created_tests_dir = False
    if not tests_dir.exists():
        tests_dir.mkdir(parents=True)
        created_tests_dir = True

    test_name = config.test_file.stem
    injected: list[Path] = []

    try:
        dest = tests_dir / config.test_file.name
        shutil.copy(config.test_file, dest)
        injected.append(dest)

        for extra in config.extra_files:
            extra_dest = tests_dir / extra.name
            shutil.copy(extra, extra_dest)
            injected.append(extra_dest)

        cmd = [
            *config.command.split(),
            "--test",
            test_name,
            "-v",
            f"--manifest-path={crate_dir / 'Cargo.toml'}",
        ]
        if config.extra_args:
            cmd.extend(config.extra_args.split())
        cmd.extend(["--", "--test-threads=1"])

        env = {**os.environ, **config.env}
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout + 30,
            cwd=str(workspace_root),
            env=env,
            check=False,
        )
        return parse_cargo_test_output(proc.stdout, proc.stderr, proc.returncode)

    except subprocess.TimeoutExpired:
        # Timeout is infra failure, not a wrong answer: set error so the trial is
        # invalidated rather than scored as a failed test.
        return RunResult(
            tests=[TestResult(name="timeout", passed=False, detail="cargo test timed out")],
            error=f"rust (cargo) tests timed out after {config.timeout}s",
            returncode=1,
        )
    finally:
        for f in injected:
            f.unlink(missing_ok=True)
        if created_tests_dir:
            with contextlib.suppress(OSError):
                tests_dir.rmdir()


def parse_cargo_test_output(stdout: str, stderr: str, returncode: int) -> RunResult:
    """Parse cargo test output into RunResult.

    Cargo prints test result lines to stdout but compilation errors to
    stderr, so we search combined output. The ``failures:`` section
    (delimited by dashes) contains per-test failure details.
    """
    combined = stdout + "\n" + stderr
    pattern = re.compile(r"^test\s+(.+?)\s+\.\.\.\s+(ok|FAILED|ignored)", re.MULTILINE)
    tests: list[TestResult] = []

    for m in pattern.finditer(combined):
        name, status = m.group(1), m.group(2)
        if status == "ignored":
            continue
        tests.append(
            TestResult(
                name=name,
                passed=status == "ok",
                detail="" if status == "ok" else _extract_failure(name, combined),
            )
        )

    if not tests and returncode != 0:
        detail = stderr[:300] if stderr else stdout[:300]
        return RunResult(
            tests=[
                TestResult(
                    name="build_error",
                    passed=False,
                    detail=f"cargo test failed (exit {returncode}): {detail}",
                )
            ],
            raw_stdout=stdout,
            raw_stderr=stderr,
            returncode=returncode,
        )

    return fail_closed_on_exit_code(RunResult(tests=tests, raw_stdout=stdout, raw_stderr=stderr, returncode=returncode))


def _extract_failure(test_name: str, combined: str) -> str:
    """Extract failure details from the ``failures:`` section of cargo output.

    The section is delimited by a line of dashes after ``failures:`` and
    ends at the next ``failures:`` or ``test result:`` line.
    """
    failures_section = re.search(
        r"^failures:\s*\n-+\n(.+?)(?:^failures:|^test result:)",
        combined,
        re.MULTILINE | re.DOTALL,
    )
    if failures_section:
        for line in failures_section.group(1).splitlines():
            if test_name in line:
                return line.strip()[:200]
    return "FAILED"
