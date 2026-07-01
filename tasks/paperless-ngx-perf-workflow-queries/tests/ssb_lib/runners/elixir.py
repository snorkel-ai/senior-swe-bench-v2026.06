"""Elixir test runner — runs verify*_test.exs files via `mix test`."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import register
from .base import BaseRunner, RunConfig, RunResult, TestResult


class ElixirRunConfig(RunConfig):
    """Configuration for running ExUnit tests via mix."""

    app: str
    umbrella_root: str = ""
    command: str = "mix test"
    extra_args: str = ""


@register
class ElixirRunner(BaseRunner):
    name = "elixir"

    def discover(self, verify_dir: Path) -> list[Path]:
        return sorted(verify_dir.glob("verify*_test.exs"))

    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> ElixirRunConfig:
        mix_cfg = verify_toml.get("mix", {})
        return ElixirRunConfig(
            test_file=test_file,
            repo_dir=repo_dir,
            app=mix_cfg.get("app", ""),
            umbrella_root=mix_cfg.get("umbrella_root", ""),
            timeout=mix_cfg.get("timeout", 300),
            env=mix_cfg.get("env", {}),
            command=mix_cfg.get("command", "mix test"),
            extra_args=mix_cfg.get("extra_args", ""),
        )

    def run_file(self, config: ElixirRunConfig) -> RunResult:
        return run(config)


def run(config: ElixirRunConfig) -> RunResult:
    """Copy test into app/test/__verification__/, run mix test, parse, clean up."""
    app_dir = config.repo_dir / config.app
    run_dir = config.repo_dir / config.umbrella_root if config.umbrella_root else app_dir

    verification_dir = app_dir / "test" / "__verification__"
    verification_dir.mkdir(parents=True, exist_ok=True)

    injected: list[Path] = []
    try:
        dest = verification_dir / config.test_file.name
        shutil.copy(config.test_file, dest)
        injected.append(dest)

        for extra in config.extra_files:
            extra_dest = verification_dir / extra.name
            shutil.copy(extra, extra_dest)
            injected.append(extra_dest)

        rel_path = dest.relative_to(run_dir)
        cmd = [*config.command.split(), str(rel_path), "--trace", "--no-color"]
        if config.extra_args:
            cmd.extend(config.extra_args.split())

        env = {**os.environ, "MIX_ENV": "test", **config.env}
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout + 60,
            cwd=str(run_dir),
            env=env,
            check=False,
        )
        return parse_mix_test_output(proc.stdout, proc.stderr, proc.returncode)

    except subprocess.TimeoutExpired:
        # Timeout is infra failure, not a wrong answer: set error so the trial is
        # invalidated rather than scored as a failed test.
        return RunResult(
            tests=[TestResult(name="timeout", passed=False, detail="mix test timed out")],
            error=f"elixir (mix) tests timed out after {config.timeout}s",
            returncode=1,
        )
    finally:
        for f in injected:
            f.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            verification_dir.rmdir()


def parse_mix_test_output(stdout: str, stderr: str, returncode: int) -> RunResult:
    """Parse ``mix test --trace`` output into a RunResult.

    Correctness rests on the two signals ExUnit emits reliably — the
    ``N tests, M failures`` summary line and the process exit code — NOT on
    matching every test name (ExUnit names are free-form multi-word sentences).
    Names are extracted only for diagnostics; counts come from the summary and
    are reconciled with the exit code.
    """
    # Final "N tests, M failures[, ...]" summary line (last one wins).
    summaries = re.findall(r"(\d+)\s+tests?,\s+(\d+)\s+failures?", stdout)
    if not summaries:
        # No summary line: a compile/early failure or anomalous run. Never a pass.
        detail = extract_early_failure(stdout, stderr) or f"mix test produced no test summary (exit {returncode})"
        return RunResult(
            tests=[TestResult(name="error", passed=False, detail=detail)],
            raw_stdout=stdout,
            raw_stderr=stderr,
            returncode=returncode,
        )

    total = int(summaries[-1][0])
    failed_count = int(summaries[-1][1])

    # Exit-code authority: mix test exits non-zero iff a test failed, was
    # invalid, or compilation failed. Summary says zero failures but process
    # failed → fail closed.
    if returncode != 0 and failed_count == 0:
        failed_count = 1

    if total == 0:
        # No tests ran — broken verify file, not a vacuous pass. Emit no_tests.
        return RunResult(tests=[], raw_stdout=stdout, raw_stderr=stderr, returncode=returncode)

    # Diagnostic names, de-duped preserving first-seen order.
    failed_names = list(
        dict.fromkeys(
            m.group(1).strip() for m in re.finditer(r"^\s*\d+\)\s+test\s+(.+?)\s+\([\w.]+\)\s*$", stdout, re.MULTILINE)
        )
    )
    ran_names = list(
        dict.fromkeys(
            m.group(1).strip() for m in re.finditer(r"^\s*\*\s+test\s+(.+?)\s+\([\d.]+m?s\)", stdout, re.MULTILINE)
        )
    )
    failed_set = set(failed_names)
    passed_names = [n for n in ran_names if n not in failed_set]

    # Counts are authoritative (summary + exit code); names are best-effort.
    tests: list[TestResult] = []
    for i in range(failed_count):
        name = failed_names[i] if i < len(failed_names) else f"failure #{i + 1}"
        tests.append(TestResult(name=name, passed=False, detail="FAILED"))
    for i in range(max(total - failed_count, 0)):
        name = passed_names[i] if i < len(passed_names) else f"test #{i + 1}"
        tests.append(TestResult(name=name, passed=True))

    return RunResult(tests=tests, raw_stdout=stdout, raw_stderr=stderr, returncode=returncode)


def extract_early_failure(stdout: str, stderr: str) -> str:
    """Extract compile or syntax errors from mix output.

    Returns the full match including the error type (e.g.
    ``** (CompileError) test/my_test.exs:3: undefined function foo/0``).
    """
    combined = stdout + "\n" + stderr
    for pattern in [
        r"\*\*\s+\((CompileError)\)[^\n]*(?:\n[^\n]+)?",
        r"\*\*\s+\((SyntaxError)\)[^\n]*",
        r"\*\*\s+\((UndefinedFunctionError)\)[^\n]*",
        r"\*\*\s+\(([A-Za-z]+Error)\)[^\n]*",
    ]:
        m = re.search(pattern, combined)
        if m:
            return m.group(0).strip().replace("\n", " ")[:200]
    return ""
