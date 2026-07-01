"""JavaScript/TypeScript test runner — runs verify*.test.ts files via Jest or Vitest."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import register
from .base import BaseRunner, RunConfig, RunResult, TestResult, fail_closed_on_exit_code


class JsRunConfig(RunConfig):
    """Configuration for running Jest or Vitest tests."""

    config_dir: str = ""
    test_parent: str = ""
    runner: str = "jest"
    command: str = "npx jest"
    test_path_flag: str = "--testPathPattern"


@register
class JsRunner(BaseRunner):
    name = "js"

    def discover(self, verify_dir: Path) -> list[Path]:
        files: list[Path] = []
        for pattern in ("verify*.test.ts", "verify*.test.tsx", "verify*.test.js"):
            files.extend(verify_dir.glob(pattern))
        return sorted(files)

    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> JsRunConfig:
        js_cfg = verify_toml.get("jest", {})
        return JsRunConfig(
            test_file=test_file,
            repo_dir=repo_dir,
            config_dir=js_cfg.get("config_dir", ""),
            test_parent=js_cfg.get("test_parent", ""),
            runner=js_cfg.get("runner", "jest"),
            command=js_cfg.get("command", "npx jest"),
            timeout=js_cfg.get("timeout", 300),
            env=js_cfg.get("env", {}),
            test_path_flag=js_cfg.get("test_path_flag", "--testPathPattern"),
        )

    def run_file(self, config: JsRunConfig) -> RunResult:
        return run(config)


def run(config: JsRunConfig) -> RunResult:
    """Copy test file into source tree, run jest/vitest --json, parse, cleanup."""
    run_dir = config.repo_dir / config.config_dir if config.config_dir else config.repo_dir
    dest_dir = config.repo_dir / config.test_parent if config.test_parent else run_dir / "__verification__"
    dest_dir.mkdir(parents=True, exist_ok=True)

    injected: list[Path] = []
    try:
        dest = dest_dir / config.test_file.name
        shutil.copy(config.test_file, dest)
        injected.append(dest)

        for extra in config.extra_files:
            extra_dest = dest_dir / extra.name
            shutil.copy(extra, extra_dest)
            injected.append(extra_dest)

        env = {
            **os.environ,
            "NODE_ENV": "test",
            "NODE_OPTIONS": "--max-old-space-size=4096",
            **config.env,
        }

        if config.runner == "vitest":
            cmd = [
                "npx",
                "vitest",
                "run",
                str(dest),
                "--reporter=json",
                f"--testTimeout={config.timeout * 1000}",
            ]
        else:
            cmd = [
                *config.command.split(),
                config.test_path_flag,
                str(dest),
                "--json",
                "--no-cache",
                "--forceExit",
                f"--testTimeout={config.timeout * 1000}",
            ]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout + 60,
            cwd=str(run_dir),
            env=env,
            check=False,
        )

        result = parse_jest_json(proc.stdout, proc.returncode)

        result.raw_stdout = proc.stdout
        result.raw_stderr = proc.stderr
        result.returncode = proc.returncode
        return result

    except subprocess.TimeoutExpired:
        # Timeout is infra failure, not a wrong answer: set error so the trial is
        # invalidated rather than scored as a failed test.
        return RunResult(
            tests=[TestResult(name="timeout", passed=False, detail="jest/vitest timed out")],
            error=f"js (jest/vitest) tests timed out after {config.timeout}s",
            returncode=1,
        )
    finally:
        for f in injected:
            f.unlink(missing_ok=True)
        if not config.test_parent and dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)


def parse_jest_json(stdout: str, returncode: int) -> RunResult:
    """Parse Jest --json output into RunResult."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        for line in stdout.splitlines():
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        else:
            return parse_jest_text(stdout, returncode)

    tests: list[TestResult] = []
    for suite in data.get("testResults", []):
        for assertion in suite.get("assertionResults", []):
            name = assertion.get("fullName", assertion.get("title", "unknown"))
            status = assertion.get("status", "")
            passed = status == "passed"
            detail = ""
            if not passed:
                messages = assertion.get("failureMessages", [])
                detail = messages[0][:500] if messages else status
            tests.append(TestResult(name=name, passed=passed, detail=detail))

    if not tests and returncode != 0:
        return RunResult(
            tests=[
                TestResult(
                    name="error",
                    passed=False,
                    detail=f"jest failed (exit {returncode}): {stdout[:500]}",
                )
            ],
            returncode=returncode,
        )

    return fail_closed_on_exit_code(RunResult(tests=tests, returncode=returncode))


def parse_jest_text(stdout: str, returncode: int) -> RunResult:
    """Fallback text parsing for Jest/Vitest output."""
    passed_m = re.search(r"(\d+)\s+passed", stdout)
    failed_m = re.search(r"(\d+)\s+failed", stdout)
    passed_count = int(passed_m.group(1)) if passed_m else 0
    failed_count = int(failed_m.group(1)) if failed_m else 0

    tests: list[TestResult] = []
    for i in range(passed_count):
        tests.append(TestResult(name=f"test_{i}", passed=True))
    for i in range(failed_count):
        tests.append(TestResult(name=f"test_{passed_count + i}", passed=False, detail="FAILED"))

    if not tests and returncode != 0:
        return RunResult(
            tests=[TestResult(name="error", passed=False, detail=f"test runner failed (exit {returncode})")],
            returncode=returncode,
        )

    return fail_closed_on_exit_code(RunResult(tests=tests, returncode=returncode))
