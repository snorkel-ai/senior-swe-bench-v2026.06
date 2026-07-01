"""Python (pytest) runner for verify.py files."""

from __future__ import annotations

import os
import shlex
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from . import register
from .base import BaseRunner, RunConfig, RunResult, TestResult


class PythonRunConfig(RunConfig):
    """Configuration for running pytest."""

    logs_dir: Path = Path("/logs/verifier")
    command: str = ""


@register
class PythonRunner(BaseRunner):
    name = "python"

    def discover(self, verify_dir: Path) -> list[Path]:
        vpy = verify_dir / "verify.py"
        return [vpy] if vpy.exists() else []

    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> PythonRunConfig:
        pytest_cfg = verify_toml.get("pytest", {})
        return PythonRunConfig(
            test_file=test_file,
            repo_dir=repo_dir,
            logs_dir=logs_dir,
            timeout=pytest_cfg.get("timeout", pytest_cfg.get("timeout_sec", 300)),
            env=pytest_cfg.get("env", {}),
            command=pytest_cfg.get("command", ""),
        )

    def run_file(self, config: PythonRunConfig) -> RunResult:
        return run(config)


def run(config: PythonRunConfig) -> RunResult:
    """Run pytest on a verify.py file and parse JUnit XML output."""
    xml_path = config.logs_dir / "pytest_results.xml"

    if config.command:
        cmd = shlex.split(config.command)
    else:
        sys_python = os.environ.get("_SYS_PYTHON", "python3")
        cmd = [sys_python, "-m", "pytest"]

    cmd += [str(config.test_file), "-v", "--tb=short", f"--junitxml={xml_path}"]

    env = {**os.environ, **config.env}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout + 30,
            cwd=str(config.repo_dir),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # Timeout is infra failure, not a wrong answer: set error so the trial is
        # invalidated rather than scored as a failed test.
        return RunResult(
            tests=[TestResult(name="timeout", passed=False, detail="pytest timed out")],
            error=f"pytest tests timed out after {config.timeout}s",
            returncode=1,
        )

    if not xml_path.exists():
        return RunResult(
            tests=[TestResult(name="pytest", passed=False, detail="no XML output")],
            error=f"pytest produced no JUnit XML (exit {proc.returncode})",
            raw_stdout=proc.stdout,
            raw_stderr=proc.stderr,
            returncode=proc.returncode,
        )

    result = parse_junit_xml(xml_path)
    result.raw_stdout = proc.stdout
    result.raw_stderr = proc.stderr
    result.returncode = proc.returncode
    return result


def parse_junit_xml(xml_path: Path) -> RunResult:
    """Parse pytest JUnit XML into RunResult."""
    tree = ET.parse(str(xml_path))  # noqa: S314
    root = tree.getroot()
    suite_elem = root.find("testsuite")
    suite = suite_elem if suite_elem is not None else root

    tests: list[TestResult] = []
    for tc in suite.findall("testcase"):
        name = tc.get("name", "")
        failed = tc.find("failure") is not None
        errored = tc.find("error") is not None
        detail = ""
        if failed:
            detail = (tc.find("failure").get("message", "") or "")[:200]  # type: ignore[union-attr]
        elif errored:
            detail = (tc.find("error").get("message", "") or "")[:200]  # type: ignore[union-attr]
        tests.append(TestResult(name=name, passed=not (failed or errored), detail=detail))

    return RunResult(tests=tests)
