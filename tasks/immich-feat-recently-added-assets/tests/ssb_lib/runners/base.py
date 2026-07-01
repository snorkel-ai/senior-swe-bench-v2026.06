"""Base types and ABC for multi-language test runners."""

from __future__ import annotations

import abc
from pathlib import Path

from pydantic import BaseModel, Field


class TestResult(BaseModel):
    """Result of a single test function/case."""

    __test__ = False  # prevent pytest collection

    name: str
    passed: bool
    detail: str = ""
    stdout: str = ""
    stderr: str = ""


class RunResult(BaseModel):
    """Aggregate result of running one or more verify files via a single runner."""

    tests: list[TestResult] = Field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""
    returncode: int = 0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and len(self.tests) > 0 and all(t.passed for t in self.tests)

    @property
    def outcome(self) -> str:
        """Authoritative pass/fail classification.

        'error'    — runner crashed / toolchain missing (self.error set).
        'no_tests' — ran but produced zero test results; must NOT be treated as
                     a vacuous pass.
        'pass'     — ran, >=1 test, all passed.
        'fail'     — ran, >=1 test, at least one failed.
        """
        if self.error is not None:
            return "error"
        if not self.tests:
            return "no_tests"
        return "pass" if all(t.passed for t in self.tests) else "fail"

    @property
    def passed_count(self) -> int:
        return sum(1 for t in self.tests if t.passed)

    @property
    def total_count(self) -> int:
        return len(self.tests)

    def to_verifier_format(self, prefix: str = "") -> dict[str, dict]:
        """Convert to the verifier_results.json test entry format."""
        tests: dict[str, dict] = {}
        for t in self.tests:
            key = f"{prefix}::{t.name}" if prefix else t.name
            entry: dict = {"pass": t.passed}
            if not t.passed and t.detail:
                entry["failure"] = t.detail[:500]
            tests[key] = entry
        return tests


def fail_closed_on_exit_code(result: RunResult) -> RunResult:
    """Make a non-zero process exit authoritative over the per-test parse.

    ``go test`` / ``cargo test`` / ``jest`` / ``mix test`` all exit non-zero iff
    a test failed, the build failed, or the suite errored. If the parser yielded
    tests that ALL passed yet the process exited non-zero, the parse missed a
    real failure (multi-word test name, suite-level error, post-run panic), so
    append a synthetic failure rather than score a vacuous pass.

    No-op when the exit code is zero, a failing test was already found, or no
    tests were produced. Pytest is intentionally NOT routed through here: its
    JUnit XML is authoritative and pytest can exit non-zero benignly (e.g.
    warnings-as-errors), which must not become a false negative.
    """
    if result.returncode != 0 and result.tests and all(t.passed for t in result.tests):
        result.tests.append(
            TestResult(
                name=f"runner exit {result.returncode}",
                passed=False,
                detail=(
                    f"process exited {result.returncode} but the parser found no failing "
                    "test — failure not attributed to a named test"
                ),
            )
        )
    return result


class RunConfig(BaseModel):
    """Base config shared by all runners."""

    test_file: Path
    repo_dir: Path = Path()
    timeout: int = 300
    env: dict[str, str] = Field(default_factory=dict)
    extra_files: list[Path] = Field(default_factory=list)


class BaseRunner(abc.ABC):
    """Abstract base for language-specific test runners."""

    name: str = ""

    @abc.abstractmethod
    def discover(self, verify_dir: Path) -> list[Path]:
        """Return verify files this runner handles."""

    @abc.abstractmethod
    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> RunConfig:
        """Construct a typed config for this runner from verify.toml settings."""

    @abc.abstractmethod
    def run_file(self, config: RunConfig) -> RunResult:
        """Execute a single verify file and return structured results."""
