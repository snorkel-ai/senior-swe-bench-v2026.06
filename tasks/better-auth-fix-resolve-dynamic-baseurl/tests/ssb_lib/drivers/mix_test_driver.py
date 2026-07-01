"""Mix test driver — runs validation-agent-generated ExUnit test files via `mix test`.

The validation agent writes a `_test.exs` file like:

    defmodule StoryXyzTest do
      use ExUnit.Case
      Code.require_file("validation_params.exs", __DIR__)

      for {case_data, idx} <- Enum.with_index(ValidationParams.get_test_cases()) do
        @case_data case_data
        @idx idx
        test "case_#{@idx}" do
          inputs = @case_data["inputs"]
          expected = @case_data["expected"]
          # exercise code under test
          assert MyModule.compute(inputs["x"]) == expected["y"]
        end
      end
    end

The helper uses OTP's stdlib `:json.decode/1` (OTP 27+), so no Jason
dep is required. The helper file is driver-generated and placed next
to the validation agent's test file; the validation agent cannot modify the expected values.

Story-level fields:
- `mix_app`: path to the mix project relative to repo (e.g. "packages/sync-service").
  Required.
- `mix_umbrella_root`: path from which to invoke `mix test`. Defaults to
  `mix_app`. Set this when the app lives inside an umbrella and tests must
  run from the umbrella root (optional).
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import CaseResult, Driver, DriverResult, Execution

# Elixir helper module written alongside the validation agent's test file. Reads params via OTP's
# stdlib :json; the %{null: nil} decoder option maps JSON null to Elixir nil
# (the default would be the :null atom).
PARAMS_HELPER_TEMPLATE = """\
# Driver-generated validation params helper.
# DO NOT MODIFY. Reads test parameters from an immutable JSON file
# whose path is supplied via the VALIDATION_PARAMS env var.
defmodule ValidationParams do
  def get_test_cases do
    path =
      System.get_env("VALIDATION_PARAMS") ||
        raise "VALIDATION_PARAMS env var not set"

    data = File.read!(path)
    {decoded, :ok, _rest} = :json.decode(data, :ok, %{null: nil})
    Map.fetch!(decoded, "test_cases")
  end
end
"""

_MAX_FAILURE_SNIPPET_LINES = 4


class MixTestDriver(Driver):
    """Runs validation-agent-generated ExUnit test files via `mix test`."""

    script_extension = "_test.exs"

    def execute_story(
        self,
        story: dict,
        scripts_dir: Path,
        logs_dir: Path,
        repo_dir: Path,
        timeout: int = 300,
        spec_settings: dict | None = None,
    ) -> DriverResult:
        from ..runners.elixir import ElixirRunConfig, run

        sid = story["id"]
        test_cases = self._get_test_cases(story)
        result = DriverResult(story_id=sid)

        mix_app = story.get("mix_app", "")
        mix_umbrella_root = story.get("mix_umbrella_root", "")
        app_dir = repo_dir / mix_app if mix_app else repo_dir
        run_dir = repo_dir / mix_umbrella_root if mix_umbrella_root else app_dir

        if not mix_app or not app_dir.exists():
            result.cases.append(
                CaseResult(
                    case_index=0,
                    passed=False,
                    reason=f"mix_app '{mix_app}' not found at {app_dir}",
                    ran=Execution.no,
                )
            )
            return result

        if mix_umbrella_root and not run_dir.exists():
            result.cases.append(
                CaseResult(
                    case_index=0,
                    passed=False,
                    reason=f"mix_umbrella_root '{mix_umbrella_root}' not found at {run_dir}",
                    ran=Execution.no,
                )
            )
            return result

        script_path = self._get_script_path(story, scripts_dir)
        if not script_path.exists():
            result.cases.append(
                CaseResult(case_index=0, passed=False, reason="Test file not generated", ran=Execution.no)
            )
            return result

        if not test_cases:
            result.cases.append(CaseResult(case_index=0, passed=False, reason="No test cases", ran=Execution.no))
            return result

        params_path = self.write_params_file(test_cases, logs_dir, sid)
        helper_path = logs_dir / "validation_params.exs"
        helper_path.write_text(PARAMS_HELPER_TEMPLATE)

        mix_settings = (spec_settings or {}).get("mix-test", {})
        run_env = {"VALIDATION_PARAMS": str(params_path)}
        for k, v in mix_settings.get("env", {}).items():
            run_env[str(k)] = str(v)

        try:
            run_result = run(
                ElixirRunConfig(
                    test_file=script_path,
                    repo_dir=repo_dir,
                    app=mix_app,
                    umbrella_root=mix_umbrella_root,
                    timeout=timeout,
                    env=run_env,
                    command=mix_settings.get("command", "mix test"),
                    extra_args=mix_settings.get("extra_args", ""),
                    extra_files=[helper_path],
                )
            )
        finally:
            helper_path.unlink(missing_ok=True)

        diag_dir = logs_dir / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        (diag_dir / f"{sid}_mix_stdout.txt").write_text(run_result.raw_stdout[-5000:])
        (diag_dir / f"{sid}_mix_stderr.txt").write_text(run_result.raw_stderr[-5000:])

        # Parse case_N indices directly from raw output (the generic runner
        # tracks by test name; validation needs richer per-case extraction).
        result.cases = _parse_cases_from_raw_output(
            run_result.raw_stdout,
            run_result.raw_stderr,
            run_result.returncode,
            len(test_cases),
        )
        return result


def _parse_cases_from_raw_output(
    stdout: str,
    stderr: str,
    returncode: int,
    expected_cases: int,
) -> list[CaseResult]:
    """Parse `mix test --trace` output to extract per-case pass/fail.

    Trace output format:
        ModuleTest [test/path_test.exs]
          * test case_0 (1.5ms) [L#3]
          * test case_1 [L#6]  * test case_1 (3.5ms) [L#6]

          1) test case_1 (ModuleTest)
             test/path_test.exs:6
             Assertion with == failed
             ...

        Finished in 0.02 seconds
        3 tests, 1 failure

    The trace lists every case that ran; numbered failure blocks appear only
    for failing cases and override the trace's pass status.
    """
    combined = stdout + "\n" + stderr

    # Case numbers that appeared in the trace (ran).
    ran: set[int] = set()
    for m in re.finditer(r"\*\s+test\s+case_(\d+)\b", combined):
        ran.add(int(m.group(1)))

    # Case numbers in numbered failure blocks ("N) test case_M (Module)"
    # followed by indented lines until the next block/summary/end).
    failed: dict[int, str] = {}
    failure_block_re = re.compile(
        r"^\s*\d+\)\s+test\s+case_(\d+)\s+\([^)]*\)\s*$",
        re.MULTILINE,
    )
    matches = list(failure_block_re.finditer(combined))
    for i, m in enumerate(matches):
        case_idx = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(combined)
        block = combined[start:end]
        summary_m = re.search(r"^\s*Finished in ", block, re.MULTILINE)
        if summary_m:
            block = block[: summary_m.start()]
        failed[case_idx] = _extract_failure_reason(block)

    # No cases collected with a non-zero exit means mix test failed before
    # running — most commonly a compile error.
    early_failure: str | None = None
    if not ran and not failed and returncode != 0:
        early_failure = _extract_early_failure(combined, returncode)

    # `mix test` exits non-zero iff a test failed or the suite errored. If
    # rc != 0 but the failure-block regex attributed zero failures, the
    # per-case parse is unreliable, so fail closed rather than infer "ran → PASS".
    unattributed_failure = returncode != 0 and not failed

    cases: list[CaseResult] = []
    for i in range(expected_cases):
        if i in failed:
            cases.append(
                CaseResult(
                    case_index=i,
                    passed=False,
                    reason=f"Case {i}: {failed[i]}",
                    stdout=stdout[-500:],
                    stderr=stderr[-500:],
                    ran=Execution.yes,
                )
            )
        elif i in ran and not unattributed_failure:
            cases.append(CaseResult(case_index=i, passed=True, ran=Execution.yes))
        else:
            if i in ran:
                reason = f"mix test exited {returncode} but no per-case failure was parsed — failing closed"
                ran_state = Execution.yes
            else:
                reason = early_failure or f"not found in mix test output (exit {returncode})"
                ran_state = Execution.unsure
            cases.append(
                CaseResult(
                    case_index=i,
                    passed=False,
                    reason=f"Case {i}: {reason}",
                    stdout=stdout[-500:],
                    stderr=stderr[-500:],
                    ran=ran_state,
                )
            )
    return cases


def _extract_early_failure(combined: str, returncode: int) -> str:
    """Classify failures where mix test never reached the test cases.

    Returns a short reason like "CompileError: ...". Falls back to
    a generic exit-code message when no specific marker is found.
    """
    patterns = [
        (r"\*\*\s+\((CompileError)\)[^\n]*(?:\n[^\n]+)?", "CompileError"),
        (r"\*\*\s+\((SyntaxError)\)[^\n]*", "SyntaxError"),
        (r"\*\*\s+\((UndefinedFunctionError)\)[^\n]*", "UndefinedFunctionError"),
        (r"\*\*\s+\(([A-Za-z]+Error)\)[^\n]*", "error"),
    ]
    for regex, _label in patterns:
        m = re.search(regex, combined)
        if m:
            snippet = m.group(0).strip().replace("\n", " ")
            return snippet[:200]
    return f"mix test exited {returncode} before running cases"


def _extract_failure_reason(block: str) -> str:
    """Extract a short failure reason from a numbered failure block.

    Block lines look like:
         test/path_test.exs:6
         Assertion with == failed
         code:  assert 1 + 1 == 3
         left:  2
         right: 3
         stacktrace:
           ...

    Take the first non-empty lines after the file:line, up to the
    stacktrace marker.
    """
    lines = []
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("stacktrace:"):
            break
        if re.match(r"^.+_test\.exs:\d+$", line):
            continue
        lines.append(line)
        if len(lines) >= _MAX_FAILURE_SNIPPET_LINES:
            break
    return "; ".join(lines) if lines else "test failed"
