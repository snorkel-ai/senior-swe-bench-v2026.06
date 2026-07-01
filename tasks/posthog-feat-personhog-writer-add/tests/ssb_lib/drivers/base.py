"""Base driver interface for validation execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Execution(str, Enum):
    """Whether the runner actually executed a case. Subclassing ``str`` keeps the
    wire values and JSON serialization plain strings."""

    yes = "yes"  # the runner produced a real pass/fail for this case
    unsure = "unsure"  # ambiguous — timed out, or absent from a non-zero run
    no = "no"  # never ran — no script, no test cases, missing setup precondition


@dataclass
class CaseResult:
    """Result of a single test case execution."""

    case_index: int
    passed: bool
    reason: str = ""
    stdout: str = ""
    stderr: str = ""
    ran: Execution = Execution.unsure


@dataclass
class DriverResult:
    """Result of executing all test cases for a story."""

    story_id: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return bool(self.cases) and all(c.passed for c in self.cases)

    @property
    def execution(self) -> Execution:
        """Story-level rollup: ``yes`` iff every case ran, ``no`` iff none did,
        else ``unsure`` (partial, or any unattributable case)."""
        if all(c.ran == Execution.yes for c in self.cases) and self.cases:
            return Execution.yes
        if all(c.ran == Execution.no for c in self.cases):
            return Execution.no
        return Execution.unsure

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.cases)


class Driver:
    """Base class for validation drivers.

    Bridges the validation spec format to a native test framework: writes an
    immutable params file, sets up param injection, runs the test, parses results.
    """

    # File extension the validation agent should generate.
    script_extension: str = ".py"

    def execute_story(
        self,
        story: dict,
        scripts_dir: Path,
        logs_dir: Path,
        repo_dir: Path,
        timeout: int = 300,
        spec_settings: dict | None = None,
    ) -> DriverResult:
        """Execute all test cases for a story. Subclasses must implement."""
        raise NotImplementedError

    def write_params_file(
        self,
        test_cases: list[dict],
        logs_dir: Path,
        story_id: str,
    ) -> Path:
        """Write the immutable params file for a story, returning its path."""
        params = {
            "story_id": story_id,
            "test_cases": _convert_null_sentinels(test_cases),
        }
        params_dir = logs_dir / "validation_params"
        params_dir.mkdir(parents=True, exist_ok=True)
        params_path = params_dir / f"{story_id}.json"
        params_path.write_text(json.dumps(params, indent=2))
        params_path.chmod(0o444)
        return params_path

    def _get_test_cases(self, story: dict) -> list[dict]:
        """Extract test cases from a story, normalizing single/list."""
        cases = story.get("test_case", [])
        if isinstance(cases, dict):
            cases = [cases]
        return cases

    def _get_script_path(self, story: dict, scripts_dir: Path) -> Path:
        """Get the expected path for the validation agent's generated script."""
        sid = story["id"]
        return scripts_dir / f"{sid}{self.script_extension}"


# Shared TypeScript helper for JS/TS-based drivers (Jest, Playwright).
PARAMS_HELPER_TS_TEMPLATE = """\
// Driver-generated validation params helper.
// DO NOT MODIFY. Reads test parameters from an immutable JSON file.
import * as fs from 'fs';

const PARAMS_PATH = process.env.VALIDATION_PARAMS!;
if (!PARAMS_PATH) {
  throw new Error('VALIDATION_PARAMS environment variable not set');
}

let _params: any = null;

export function loadParams(): any {
  if (!_params) {
    _params = JSON.parse(fs.readFileSync(PARAMS_PATH, 'utf-8'));
  }
  return _params;
}

export function getTestCases(): Array<{inputs: any; expected: any}> {
  return loadParams().test_cases || [];
}
"""


def map_run_result_to_cases(run_result, expected_count: int, runner_label: str = "test") -> list[CaseResult]:
    """Map a runner's TestResults to validation CaseResults by case_N index.

    Matches each expected case index to a TestResult whose name contains
    ``case_N``. Unmatched cases are reported as not found with raw output.
    """
    cases: list[CaseResult] = []
    for i in range(expected_count):
        matched = next((t for t in run_result.tests if f"case_{i}" in t.name), None)
        if matched is None:
            cases.append(
                CaseResult(
                    case_index=i,
                    passed=False,
                    reason=f"Case {i}: not found in {runner_label} output (exit {run_result.returncode})",
                    stdout=run_result.raw_stdout[-500:],
                    stderr=run_result.raw_stderr[-500:],
                    ran=Execution.unsure,
                )
            )
        elif matched.passed:
            cases.append(CaseResult(case_index=i, passed=True, ran=Execution.yes))
        else:
            cases.append(
                CaseResult(
                    case_index=i,
                    passed=False,
                    reason=f"Case {i}: {matched.detail}",
                    stderr=run_result.raw_stderr[-500:],
                    ran=Execution.yes,
                )
            )
    return cases


def _convert_null_sentinels(obj: object) -> object:
    """Recursively convert "__null__" sentinel strings to None.

    TOML has no null type, so validation_spec.toml uses "__null__" as a sentinel.
    """
    if isinstance(obj, str) and obj == "__null__":
        return None
    if isinstance(obj, dict):
        return {k: _convert_null_sentinels(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_null_sentinels(item) for item in obj]
    return obj
