"""Playwright driver — runs validation-agent-generated Playwright test files with runtime-injected params.

The validation agent writes a .spec.ts file like:
    import { test, expect } from '@playwright/test';
    import { getTestCases } from './validationParams';

    const cases = getTestCases();
    cases.forEach(({ inputs, expected }, i) => {
      test(`case ${i}`, async ({ page }) => {
        await page.goto(inputs.url);
        await expect(page.locator(inputs.selector)).toHaveText(expected.text);
      });
    });

The getTestCases() helper reads from the immutable params file. The validation agent cannot
modify the expected values.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .base import PARAMS_HELPER_TS_TEMPLATE, CaseResult, Driver, DriverResult, Execution


class PlaywrightDriver(Driver):
    """Runs validation-agent-generated Playwright test files with runtime-injected params."""

    script_extension = ".spec.ts"

    def execute_story(
        self,
        story: dict,
        scripts_dir: Path,
        logs_dir: Path,
        repo_dir: Path,
        timeout: int = 600,
        spec_settings: dict | None = None,
    ) -> DriverResult:
        sid = story["id"]
        test_cases = self._get_test_cases(story)
        result = DriverResult(story_id=sid)

        pw_settings = (spec_settings or {}).get("playwright", {})

        # Find the validation agent's test file — story's custom extension first, then defaults.
        custom_ext = story.get("script_extension")
        if custom_ext:
            script_path = scripts_dir / f"{sid}{custom_ext}"
        else:
            script_path = scripts_dir / f"{sid}.spec.ts"
            if not script_path.exists():
                script_path = scripts_dir / f"{sid}.spec.js"
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

        helper_ts = scripts_dir / "validationParams.ts"
        if not helper_ts.exists():
            helper_ts.write_text(PARAMS_HELPER_TS_TEMPLATE)

        config_dir = repo_dir / pw_settings.get("config_dir", "")

        # Copy test files into a validation dir inside the project so
        # Playwright's config and module resolution work correctly.
        test_parent = pw_settings.get("test_parent", "")
        if test_parent:
            validation_dir = repo_dir / test_parent / "__validation__"
        else:
            validation_dir = config_dir / "__validation__" if config_dir != repo_dir else None

        if validation_dir:
            validation_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(script_path, validation_dir / script_path.name)
            shutil.copy(helper_ts, validation_dir / "validationParams.ts")
            pw_harness = Path("/tests/validate/playwright_test_harness.ts")
            if pw_harness.exists():
                shutil.copy(pw_harness, validation_dir / "playwright_test_harness.ts")
            script_path = validation_dir / script_path.name

        env = {**os.environ}
        env["VALIDATION_PARAMS"] = str(params_path)
        env["NODE_ENV"] = "test"
        env.setdefault("NODE_OPTIONS", "--max-old-space-size=4096")
        env.setdefault("TZ", "America/New_York")

        for k, v in pw_settings.get("env", {}).items():
            env[k] = str(v)

        # Base URL for the dev server (task's validation_setup.sh starts it).
        base_url = pw_settings.get("base_url", "")
        if base_url:
            env["BASE_URL"] = base_url

        diag_dir = logs_dir / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        json_output = diag_dir / f"{sid}_playwright_results.json"

        env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(json_output)

        pw_cmd = pw_settings.get("command", "npx playwright test").split()
        cmd = [
            *pw_cmd,
            str(script_path),
            "--reporter=json",
            f"--timeout={timeout * 1000}",
        ]
        # Pass --project only when set; otherwise Playwright uses its defaults.
        browser = pw_settings.get("browser", "")
        if browser:
            cmd.append(f"--project={browser}")

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

            (diag_dir / f"{sid}_playwright_stdout.txt").write_text(proc.stdout[-5000:])
            (diag_dir / f"{sid}_playwright_stderr.txt").write_text(proc.stderr[-5000:])

            # Copy trace/screenshot artifacts if Playwright produced them.
            traces_dir = config_dir / "test-results"
            if traces_dir.exists():
                dest = diag_dir / f"{sid}_test-results"
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(traces_dir, dest)

        except subprocess.TimeoutExpired:
            for i in range(len(test_cases)):
                result.cases.append(
                    CaseResult(
                        case_index=i,
                        passed=False,
                        reason="Playwright timed out",
                        ran=Execution.unsure,
                    )
                )
            return result

        result.cases = self._parse_playwright_json(
            json_output,
            proc.stdout,
            proc.stderr,
            proc.returncode,
            len(test_cases),
        )

        return result

    def _parse_playwright_json(
        self,
        json_path: Path,
        stdout: str,
        stderr: str,
        returncode: int,
        expected_cases: int,
    ) -> list[CaseResult]:
        """Parse Playwright JSON reporter output for per-test results."""
        json_data = None
        with contextlib.suppress(FileNotFoundError, json.JSONDecodeError, ValueError):
            json_data = json.loads(json_path.read_text())

        if json_data is None:
            return self._parse_playwright_text(stdout, stderr, returncode, expected_cases)

        # Structure: { suites: [{ specs: [{ tests: [{ results: [...] }] }] }] }
        test_results: list[dict] = []
        for suite in json_data.get("suites", []):
            self._collect_specs(suite, test_results)

        # Word-boundary matching avoids "case 1" matching "case 10".
        import re

        cases: list[CaseResult] = []
        for i in range(expected_cases):
            matched = None
            pattern = re.compile(rf"\bcase[_ ]{i}\b", re.IGNORECASE)
            for tr in test_results:
                if pattern.search(tr["name"]):
                    matched = tr
                    break
            if matched is None and i < len(test_results):
                matched = test_results[i]

            if matched is None:
                cases.append(
                    CaseResult(
                        case_index=i,
                        passed=False,
                        reason=f"Case {i}: not found in Playwright output",
                        ran=Execution.unsure,
                    )
                )
            elif matched["status"] == "expected":
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

    def _collect_specs(self, suite: dict, out: list[dict]) -> None:
        """Recursively collect spec results from nested suites."""
        for spec in suite.get("specs", []):
            title = spec.get("title", "")
            # A spec can have multiple tests (one per project/browser); take the first.
            tests = spec.get("tests", [])
            if not tests:
                continue
            test = tests[0]
            status = test.get("status", "unexpected")
            message = ""
            results = test.get("results", [])
            if results:
                last = results[-1]
                err = last.get("error", {})
                message = err.get("message", "") if isinstance(err, dict) else str(err)
            out.append({"name": title, "status": status, "message": message})

        for child in suite.get("suites", []):
            self._collect_specs(child, out)

    def _parse_playwright_text(
        self,
        stdout: str,
        stderr: str,
        returncode: int,
        expected_cases: int,
    ) -> list[CaseResult]:
        """Fallback: parse Playwright text output for pass/fail counts."""
        import re

        cases: list[CaseResult] = []
        combined = stdout + "\n" + stderr
        passed = 0
        failed = 0

        for line in combined.splitlines():
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
                        reason=f"Case {i}: no Playwright output (exit {returncode})",
                        stderr=stderr[-500:],
                        ran=Execution.unsure,
                    )
                )
        else:
            for i in range(expected_cases):
                if i < passed:
                    cases.append(CaseResult(case_index=i, passed=True, ran=Execution.yes))
                else:
                    cases.append(
                        CaseResult(
                            case_index=i,
                            passed=False,
                            reason=f"Case {i}: Playwright test failed",
                            stderr=stderr[-500:],
                            ran=Execution.yes,
                        )
                    )

        return cases
