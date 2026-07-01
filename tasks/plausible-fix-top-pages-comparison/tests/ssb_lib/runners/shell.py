"""Shell script runner — runs verify*.sh files and checks exit codes."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import register
from .base import BaseRunner, RunConfig, RunResult, TestResult


class ShellRunConfig(RunConfig):
    """Configuration for running shell scripts."""

    pass


@register
class ShellRunner(BaseRunner):
    name = "shell"

    def discover(self, verify_dir: Path) -> list[Path]:
        return sorted(verify_dir.glob("verify*.sh"))

    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> ShellRunConfig:
        shell_cfg = verify_toml.get("shell", {})
        return ShellRunConfig(
            test_file=test_file,
            repo_dir=repo_dir,
            timeout=shell_cfg.get("timeout", 300),
            env=shell_cfg.get("env", {}),
        )

    def run_file(self, config: ShellRunConfig) -> RunResult:
        try:
            proc = subprocess.run(
                ["bash", str(config.test_file)],
                capture_output=True,
                text=True,
                timeout=config.timeout,
                cwd=str(config.repo_dir),
                env={**os.environ, **config.env},
                check=False,
            )
            passed = proc.returncode == 0
            detail = "" if passed else f"exit {proc.returncode}: {proc.stderr[:200]}"
            return RunResult(
                tests=[TestResult(name=config.test_file.stem, passed=passed, detail=detail)],
                raw_stdout=proc.stdout,
                raw_stderr=proc.stderr,
                returncode=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            # Timeout is infra failure, not a wrong answer: set error so the trial
            # is invalidated rather than scored as a failed test.
            return RunResult(
                tests=[TestResult(name=config.test_file.stem, passed=False, detail="timed out")],
                error=f"shell tests timed out after {config.timeout}s",
                returncode=1,
            )
