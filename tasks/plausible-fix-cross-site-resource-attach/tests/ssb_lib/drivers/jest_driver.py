"""Jest driver — runs validation-agent-generated Jest test files with runtime-injected params.

The validation agent writes a .test.tsx file like:
    import { getTestCases } from '../validationParams';
    import { normalizeCurrency } from '@superset-ui/core';

    describe('normalizeCurrency', () => {
      const cases = getTestCases();
      test.each(cases)('case %#', ({ inputs, expected }) => {
        const result = normalizeCurrency(inputs.value);
        expect(result).toBe(expected.normalized);
      });
    });

The getTestCases() helper reads from the immutable params file. The validation agent cannot
modify the expected values.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .base import PARAMS_HELPER_TS_TEMPLATE, CaseResult, Driver, DriverResult, Execution

# CommonJS helper for Jest (which may not support ESM imports).
PARAMS_HELPER_TEMPLATE = """\
// Driver-generated validation params helper.
// DO NOT MODIFY. Reads test parameters from an immutable JSON file.
const fs = require('fs');
const path = require('path');

const PARAMS_PATH = process.env.VALIDATION_PARAMS;
if (!PARAMS_PATH) {
  throw new Error('VALIDATION_PARAMS environment variable not set');
}

let _params = null;

function loadParams() {
  if (!_params) {
    _params = JSON.parse(fs.readFileSync(PARAMS_PATH, 'utf-8'));
  }
  return _params;
}

function getTestCases() {
  return loadParams().test_cases || [];
}

module.exports = { loadParams, getTestCases };
"""


class JestDriver(Driver):
    """Runs validation-agent-generated Jest test files with runtime-injected params."""

    script_extension = ".test.tsx"

    def execute_story(
        self,
        story: dict,
        scripts_dir: Path,
        logs_dir: Path,
        repo_dir: Path,
        timeout: int = 300,
        spec_settings: dict | None = None,
    ) -> DriverResult:
        sid = story["id"]
        test_cases = self._get_test_cases(story)
        result = DriverResult(story_id=sid)

        # Story may select a config block via jest_config_key (default "jest"
        # → [settings.jest]).
        config_key = story.get("jest_config_key", "jest")
        jest_settings = (spec_settings or {}).get(config_key, {})

        # Find the validation agent's test file — story's custom extension first, then defaults.
        custom_ext = story.get("script_extension")
        if custom_ext:
            script_path = scripts_dir / f"{sid}{custom_ext}"
        else:
            script_path = scripts_dir / f"{sid}.test.tsx"
            if not script_path.exists():
                script_path = scripts_dir / f"{sid}.test.ts"
        if not script_path.exists():
            result.cases.append(
                CaseResult(
                    case_index=0,
                    passed=False,
                    reason="Test file not generated",
                    ran=Execution.no,
                )
            )
            return result

        if not test_cases:
            result.cases.append(
                CaseResult(
                    case_index=0,
                    passed=False,
                    reason="No test cases",
                    ran=Execution.no,
                )
            )
            return result

        params_path = self.write_params_file(test_cases, logs_dir, sid)

        helper_js = scripts_dir / "validationParams.js"
        helper_ts = scripts_dir / "validationParams.ts"
        if not helper_js.exists():
            helper_js.write_text(PARAMS_HELPER_TEMPLATE)
        if not helper_ts.exists():
            helper_ts.write_text(PARAMS_HELPER_TS_TEMPLATE)

        # Resolve Jest config and test directories (relative to repo_dir).
        config_dir = repo_dir / jest_settings.get("config_dir", "")
        test_parent = jest_settings.get("test_parent", "")
        if test_parent:
            validation_dir = repo_dir / test_parent / "__validation__"
        else:
            validation_dir = config_dir / "__validation__" if config_dir != repo_dir else None

        # Copy test files into the validation dir so Jest's testRegex and
        # moduleNameMapper resolve imports correctly.
        import shutil

        if validation_dir:
            validation_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(script_path, validation_dir / script_path.name)
            shutil.copy(helper_js, validation_dir / "validationParams.js")
            if helper_ts.exists():
                shutil.copy(helper_ts, validation_dir / "validationParams.ts")
            jest_harness = Path("/tests/validate/jest_test_harness.ts")
            if jest_harness.exists():
                shutil.copy(jest_harness, validation_dir / "jest_test_harness.ts")
            jest_harness_tsx = Path("/tests/validate/jest_test_harness.tsx")
            if jest_harness_tsx.exists():
                shutil.copy(jest_harness_tsx, validation_dir / "jest_test_harness.tsx")
            script_path = validation_dir / script_path.name

        env = {**os.environ}
        env["VALIDATION_PARAMS"] = str(params_path)
        env["NODE_ENV"] = "test"
        env.setdefault("NODE_OPTIONS", "--max-old-space-size=4096")
        env.setdefault("TZ", "America/New_York")

        jest_cmd = jest_settings.get("command", "npx jest").split()
        runner = jest_settings.get("runner", "jest")

        if runner == "vitest":
            # Vitest uses positional args for file filtering and supports
            # --testTimeout but NOT --json, --no-cache, or --forceExit.
            cmd = [
                *jest_cmd,
                str(script_path),
                f"--testTimeout={timeout * 1000}",
            ]
        else:
            # Jest 30+ uses --testPathPatterns (plural), Jest 29 the singular form.
            test_path_flag = jest_settings.get("test_path_flag", "--testPathPatterns")
            cmd = [
                *jest_cmd,
                test_path_flag,
                str(script_path),
                "--json",
                "--no-cache",
                "--forceExit",
                f"--testTimeout={timeout * 1000}",
            ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 60,
                cwd=str(config_dir),
                env=env,
                check=False,
            )

            diag_dir = logs_dir / "diagnostics"
            diag_dir.mkdir(parents=True, exist_ok=True)
            (diag_dir / f"{sid}_jest_stdout.txt").write_text(proc.stdout[-5000:])
            (diag_dir / f"{sid}_jest_stderr.txt").write_text(proc.stderr[-5000:])

        except subprocess.TimeoutExpired:
            for i in range(len(test_cases)):
                result.cases.append(
                    CaseResult(
                        case_index=i,
                        passed=False,
                        reason="Jest timed out",
                        ran=Execution.unsure,
                    )
                )
            return result

        result.cases = self._parse_jest_json(
            proc.stdout,
            proc.stderr,
            proc.returncode,
            len(test_cases),
        )

        return result

    def _parse_jest_json(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
        expected_cases: int,
    ) -> list[CaseResult]:
        """Parse Jest --json output for per-test results."""
        cases: list[CaseResult] = []

        import contextlib

        # Only trust a parsed object that is actually Jest's report (has a
        # `testResults` key) — a stray `console.log({...})` must not be
        # mistaken for it.
        json_data = None
        for raw_line in stdout.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("{"):
                try:
                    candidate = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and "testResults" in candidate:
                    json_data = candidate
                    break

        if json_data is None:
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                candidate = json.loads(stdout)
                if isinstance(candidate, dict) and "testResults" in candidate:
                    json_data = candidate

        if json_data is None:
            # Fall back to text parsing.  Vitest writes results to stdout
            # (not stderr), so combine both streams for the text parser.
            combined_output = stdout + "\n" + stderr
            return self._parse_jest_text(combined_output, returncode, expected_cases)

        test_results = []
        for suite in json_data.get("testResults", []):
            for test in suite.get("assertionResults", suite.get("testResults", [])):
                test_results.append(
                    {
                        "name": test.get("fullName", test.get("title", "")),
                        "status": test.get("status", ""),
                        "message": "\n".join(test.get("failureMessages", [])),
                    }
                )

        for i in range(expected_cases):
            matched = None
            for tr in test_results:
                if f"case {i}" in tr["name"].lower() or f"#{i}" in tr["name"]:
                    matched = tr
                    break
            # No positional fallback: pairing case i → test_results[i] on a name
            # mismatch risks a false-pass (suite-level error entries shift
            # indices). A false-fail ("not found") is safe.
            if matched is None:
                cases.append(
                    CaseResult(
                        case_index=i,
                        passed=False,
                        reason=f"Case {i}: not found in Jest output (no test named 'case {i}')",
                        ran=Execution.unsure,
                    )
                )
            elif matched["status"] == "passed":
                cases.append(CaseResult(case_index=i, passed=True, ran=Execution.yes))
            else:
                cases.append(
                    CaseResult(
                        case_index=i,
                        passed=False,
                        reason=f"Case {i}: {matched['message'][:200]}",
                        stderr=stderr[-500:],
                        ran=Execution.yes,
                    )
                )

        return cases

    def _parse_jest_text(
        self,
        output: str,
        returncode: int,
        expected_cases: int,
    ) -> list[CaseResult]:
        """Fallback: parse Jest/vitest text output for pass/fail.

        Accepts combined stdout+stderr (vitest writes to stdout, Jest to stderr).
        First tries per-case parsing from vitest verbose lines, then falls back
        to summary-line counting.
        """
        import re

        # Strip ANSI escape codes (vitest/Jest often emit colored output).
        output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output)

        # Per-case parsing from vitest --reporter=verbose lines, e.g.
        #   ✓ suite_name > case 0 (5ms)
        #   × suite_name > case 1 (3ms)
        per_case: dict[int, bool] = {}
        for line in output.splitlines():
            case_m = re.search(r"case\s+#?(\d+)", line, re.IGNORECASE)
            if case_m:
                idx = int(case_m.group(1))
                # Anchor to the start-of-line status symbol, not a PASS/FAIL
                # substring — a test named "…should PASS…" must not read as a pass.
                if re.match(r"\s*[✓√✔]", line):
                    per_case[idx] = True
                elif re.match(r"\s*[✕×✗✘]", line):
                    per_case[idx] = False

        if per_case and len(per_case) >= expected_cases:
            cases = []
            for i in range(expected_cases):
                if per_case.get(i, False):
                    cases.append(CaseResult(case_index=i, passed=True, ran=Execution.yes))
                else:
                    cases.append(
                        CaseResult(
                            case_index=i,
                            passed=False,
                            reason=f"Case {i}: test failed",
                            stderr=output[-500:],
                            ran=Execution.yes,
                        )
                    )
            return cases

        # Fall back to summary-line counting.
        cases = []
        passed = 0
        failed = 0

        for line in output.splitlines():
            m = re.search(r"(\d+)\s+passed", line)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+)\s+failed", line)
            if m:
                failed = int(m.group(1))

        if passed + failed == 0:
            for i in range(expected_cases):
                cases.append(
                    CaseResult(
                        case_index=i,
                        passed=False,
                        reason=f"Case {i}: no Jest output (exit {returncode})",
                        stderr=output[-500:],
                        ran=Execution.unsure,
                    )
                )
        else:
            # Can't map to specific cases: mark first N passed, rest failed.
            for i in range(expected_cases):
                if i < passed:
                    cases.append(CaseResult(case_index=i, passed=True, ran=Execution.yes))
                else:
                    cases.append(
                        CaseResult(
                            case_index=i,
                            passed=False,
                            reason=f"Case {i}: Jest test failed",
                            stderr=output[-500:],
                            ran=Execution.yes,
                        )
                    )

        return cases
