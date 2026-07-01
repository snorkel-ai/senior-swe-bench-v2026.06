"""Go test runner — runs verify*_test.go files via `go test -json`."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import Field

from . import register
from .base import BaseRunner, RunConfig, RunResult, TestResult, fail_closed_on_exit_code


class GoRunConfig(RunConfig):
    """Configuration for running Go tests."""

    package: str
    module_dir: str = ""
    run: str = ""
    build_tags: list[str] = Field(default_factory=list)
    command: str = "go test"


@register
class GoRunner(BaseRunner):
    name = "go"

    def discover(self, verify_dir: Path) -> list[Path]:
        return sorted(verify_dir.glob("verify*_test.go"))

    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> GoRunConfig:
        go_cfg = verify_toml.get("go", {})
        per_file = go_cfg.get("tests", {}).get(test_file.stem, {})
        merged = {**go_cfg, **per_file}
        merged.pop("tests", None)
        return GoRunConfig(
            test_file=test_file,
            repo_dir=repo_dir,
            package=merged.get("package", ""),
            module_dir=merged.get("module_dir", ""),
            build_tags=merged.get("build_tags", []),
            timeout=merged.get("timeout", 300),
            env=merged.get("env", {}),
            command=merged.get("command", "go test"),
        )

    def run_file(self, config: GoRunConfig) -> RunResult:
        return run(config)


def run(config: GoRunConfig) -> RunResult:
    """Inject test file into package dir, run go test -json, parse, cleanup."""
    module_root = config.repo_dir / config.module_dir if config.module_dir else config.repo_dir
    package_dir = config.repo_dir / config.package

    injected: list[Path] = []
    try:
        dest = package_dir / config.test_file.name
        shutil.copy(config.test_file, dest)
        injected.append(dest)

        for extra in config.extra_files:
            extra_dest = package_dir / extra.name
            shutil.copy(extra, extra_dest)
            injected.append(extra_dest)

        cmd = [*config.command.split(), "-json", "-v", "-count=1", f"-timeout={config.timeout}s"]
        if config.build_tags:
            cmd.extend(["-tags", ",".join(config.build_tags)])

        # Default to "^$" (matches no test) when the verify file has no
        # `func Test*`. An empty -run would instead run the package's existing
        # tests, which a nop agent could pass; "^$" collects 0 tests → INVALID.
        run_filter = config.run or find_test_func(config.test_file) or "^$"
        cmd.extend(["-run", run_filter])

        rel_package = "./" + str(package_dir.relative_to(module_root))
        cmd.append(rel_package)

        env = {**os.environ, **config.env}
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout + 30,
            cwd=str(module_root),
            env=env,
            check=False,
        )
        return parse_go_test_json(proc.stdout, proc.stderr, proc.returncode)

    except subprocess.TimeoutExpired:
        # Timeout is infra failure, not a wrong answer: set error so the trial is
        # invalidated rather than scored as a failed test.
        return RunResult(
            tests=[TestResult(name="timeout", passed=False, detail="go test timed out")],
            error=f"go tests timed out after {config.timeout}s",
            returncode=1,
        )
    finally:
        for f in injected:
            f.unlink(missing_ok=True)


def find_test_func(test_file: Path) -> str:
    """Extract Test function name(s) from Go file as a -run regex filter.

    Returns a single name if one Test function, or a ``|``-joined regex
    if multiple (e.g. ``TestA|TestB``). Returns empty string if none found.
    """
    text = test_file.read_text()
    funcs = re.findall(r"func\s+(Test\w+)\s*\(", text)
    if not funcs:
        return ""
    if len(funcs) == 1:
        return funcs[0]
    return "|".join(funcs)


def read_package_name(test_file: Path, package_dir: Path) -> str:
    """Read package name from test file or fall back to directory_test convention.

    Go external test packages use the ``dirname_test`` suffix by convention.
    If the test file declares a specific package, use that instead.
    """
    text = test_file.read_text()
    m = re.search(r"^package\s+(\w+)", text, re.MULTILINE)
    if m:
        return m.group(1)
    return package_dir.name + "_test"


def parse_go_test_json(stdout: str, stderr: str, returncode: int) -> RunResult:
    """Parse go test -json output into RunResult.

    go test -json emits events in order: run → output (multiple) → pass/fail.
    We buffer output lines per test and attach them when the terminal event
    arrives.  Package-level output (no ``Test`` field) is collected separately
    so compilation errors are not silently dropped.
    """
    output_buf: dict[str, list[str]] = {}
    tests: dict[str, TestResult] = {}
    package_output: list[str] = []

    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            package_output.append(stripped)
            continue
        action = event.get("Action", "")
        test_name = event.get("Test", "")
        if not test_name:
            if action == "output":
                text = event.get("Output", "").rstrip()
                if text:
                    package_output.append(text)
            continue

        if action == "output":
            text = event.get("Output", "").strip()
            if text:
                output_buf.setdefault(test_name, []).append(text)
        elif action == "pass":
            tests[test_name] = TestResult(name=test_name, passed=True)
        elif action == "fail":
            lines = output_buf.get(test_name, [])
            detail = "; ".join(lines[-3:]) if lines else "test failed"
            tests[test_name] = TestResult(name=test_name, passed=False, detail=detail[:500])

    if not tests and returncode != 0:
        detail_lines = [line for line in package_output if line.strip()][-5:]
        detail = "\n".join(detail_lines) if detail_lines else stderr[:300]
        return RunResult(
            tests=[
                TestResult(
                    name="build_error",
                    passed=False,
                    detail=detail[:500],
                )
            ],
            raw_stdout=stdout,
            raw_stderr=stderr,
            returncode=returncode,
        )

    return fail_closed_on_exit_code(
        RunResult(
            tests=list(tests.values()),
            raw_stdout=stdout,
            raw_stderr=stderr,
            returncode=returncode,
        )
    )
