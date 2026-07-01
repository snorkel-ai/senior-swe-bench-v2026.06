#!/usr/bin/env python3
"""Test pipeline utilities: pytest result parsing and reward aggregation.

Two entry points called by test.sh:
  python3 /tests/run_aggregate.py parse-pytest  # JUnit XML → verifier_results.json
  python3 /tests/run_aggregate.py aggregate    # all outputs → reward.json + reward.txt
"""

import json
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

LOGS_DIR = Path("/logs/verifier")


# ---------------------------------------------------------------------------
# parse-pytest: JUnit XML → verifier_results.json
# ---------------------------------------------------------------------------


def parse_pytest() -> None:
    """Parse pytest JUnit XML into structured JSON with per-test results."""
    xml_path = LOGS_DIR / "pytest_results.xml"
    json_path = LOGS_DIR / "verifier_results.json"

    if not xml_path.exists():
        json_path.write_text(
            json.dumps({"passed": 0, "total": 0, "all_pass": False, "tests": {}, "error": "no XML"}, indent=2)
        )
        return

    tree = ET.parse(str(xml_path))  # noqa: S314 — trusted local file from pytest
    root = tree.getroot()
    suite = root.find("testsuite") or root

    tests: dict = {}
    for tc in suite.findall("testcase"):
        name = tc.get("name", "")
        failed = tc.find("failure") is not None
        errored = tc.find("error") is not None
        entry: dict = {"pass": not (failed or errored)}
        if failed:
            msg = tc.find("failure").get("message", "") or ""  # type: ignore[union-attr]
            entry["failure"] = msg[:200]
        if errored:
            msg = tc.find("error").get("message", "") or ""  # type: ignore[union-attr]
            entry["error"] = msg[:200]
        tests[name] = entry

    passed = sum(1 for t in tests.values() if t["pass"])
    total = len(tests)
    json_path.write_text(
        json.dumps({"passed": passed, "total": total, "all_pass": passed == total, "tests": tests}, indent=2)
    )


# ---------------------------------------------------------------------------
# aggregate: all stage outputs → canonical reward.json + reward.txt
# ---------------------------------------------------------------------------


def aggregate() -> None:
    """Assemble reward files from individual stage outputs.

    Reads verifier_results.json, judge_output.json (rubric/taste), and
    validation_results.json, then writes:
      - reward_details.json: full nested structure for our analysis tools
      - reward.json: flat scalars only (harbor-compatible)
      - reward.txt: binary 0/1
    """
    # Read verifier results (per-test from pytest)
    verifier_path = LOGS_DIR / "verifier_results.json"
    verifier = json.loads(verifier_path.read_text()) if verifier_path.exists() else None
    verifier_pass = verifier.get("all_pass", False) if verifier else False

    # Read judge output (written by run_judge.py rubric/taste stages)
    judge_path = LOGS_DIR / "judge_output.json"
    judge = json.loads(judge_path.read_text()) if judge_path.exists() else {}

    # Read validation results (written by run_validate.py)
    validation_path = LOGS_DIR / "validation_results.json"
    validation = json.loads(validation_path.read_text()) if validation_path.exists() else None

    # Determine primary reward signal: must pass ALL present verification layers.
    verifier_total = verifier.get("total", 0) if verifier else 0
    verifier_ok = verifier_pass if verifier_total > 0 else True
    # run_verify sets runner_errors when a verifier runner crashed OR discovered
    # verify files but produced 0 tests (broken/empty verifier). That verifier
    # signal is UNTRUSTWORTHY → INVALID trial; never a vacuous 0-test pass (C2).
    verifier_runner_failure = bool(verifier.get("runner_errors")) if verifier else False

    # A feature/migration task ships a validation_spec. If validation was
    # EXPECTED but produced no results, the validation agent crashed (e.g. an
    # unhandled Bedrock 429 in the validation step). Such a trial is INVALID:
    # reward is None. We must NOT fall back to verifier-only scoring here —
    # that yields spurious passes (the verifier's nop-gating test passes, so a
    # crashed feature trial would score 1.0). reward=None signals "no result"
    # so the trial is excluded from solve rates rather than counted as a pass.
    validation_expected = Path("/tests/validate/validation_spec.toml").exists()

    reward: float | None
    if verifier_runner_failure:
        reward = None  # verifier crashed / produced 0 tests → invalid trial (C2)
    elif validation is not None:
        validation_score = validation.get("validation_score", 0)
        validation_ok = validation_score >= 1.0
        reward = 1.0 if (verifier_ok and validation_ok) else 0.0
    elif validation_expected:
        reward = None  # validation expected but crashed → invalid trial
    else:
        # Investigation task (no validation_spec): the native verifier IS the
        # signal. C1: an ABSENT verifier_results.json (verifier crashed / deps
        # failed to install) leaves verifier=None → verifier_ok defaulted True.
        # Treat that as INVALID (reward=None), NOT a vacuous 1.0 pass. Scoped to
        # this else ONLY — feature/validation tasks legitimately have no native
        # verifier and are handled by the branches above; do not invalidate them.
        reward = None if verifier is None else (1.0 if verifier_ok else 0.0)

    # ── reward_details.json: full nested structure ────────────────────────
    rubric_status = judge.get("rubric_status")
    rubric_error = judge.get("rubric_error")
    rubric_block: dict = {
        "score": judge.get("rubric_score"),
        "fail_to_pass_score": judge.get("fail_to_pass_score"),
        "pass_to_pass_score": judge.get("pass_to_pass_score"),
    }
    if rubric_status is not None:
        rubric_block["status"] = rubric_status
    if rubric_error is not None:
        rubric_block["error"] = rubric_error

    details: dict = {
        "reward": reward,
        "correctness": reward,
        "verifier": verifier or {"passed": 0, "total": 0, "all_pass": False, "tests": {}},
        "rubric": rubric_block,
    }

    # Per-criterion rubric scores
    skip_keys = {
        "reward",
        "correctness",
        "rubric_score",
        "fail_to_pass_score",
        "pass_to_pass_score",
        "patch_bloat",
        "patch_classifications",
        "taste",
        "taste_rationales",
        "practice_alignment",
        "relative_taste",
        "validation_score",
        "validation_stories",
        "rubric_all",
        "rubric_status",
        "rubric_error",
        "rubric_all_status",
        "rubric_all_error",
        "taste_status",
        "taste_error",
    }
    for k, v in judge.items():
        if k not in skip_keys:
            details["rubric"][k] = v

    # Taste section
    taste_keys = ("patch_bloat", "patch_classifications", "taste", "practice_alignment", "relative_taste")
    taste = {k: judge[k] for k in taste_keys if k in judge}
    if taste:
        details["taste"] = taste

    # Taste rationales (preserve for analysis)
    taste_rationales = judge.get("taste_rationales")
    if taste_rationales:
        details["taste_rationales"] = taste_rationales

    # rubric_all section (experimental — informational only, does not affect reward)
    rubric_all = judge.get("rubric_all")
    if rubric_all:
        details["rubric_all"] = rubric_all

    # Validation section
    if validation is not None:
        details["validation"] = {
            "score": validation.get("validation_score", 0),
            "passed_stories": validation.get("passed_stories", 0),
            "total_stories": validation.get("total_stories", 0),
            "passed_cases": validation.get("passed_cases", 0),
            "total_cases": validation.get("total_cases", 0),
            "stories": validation.get("stories", {}),
        }
        va_failure = validation.get("validation_agent_infrastructure_failure")
        if va_failure:
            details["validation"]["validation_agent_infrastructure_failure"] = va_failure

    (LOGS_DIR / "reward_details.json").write_text(json.dumps(details, indent=2))

    # ── reward.json + reward.txt ──────────────────────────────────────────
    if reward is None:
        # Validation was expected but produced no results (the validation agent
        # crashed, e.g. an unhandled Bedrock 429) — this trial is INVALID.
        # Harbor reads reward.json BEFORE reward.txt, and its reward schema is
        # dict[str, float | int]; a reward.json carrying null values raises a
        # pydantic ValidationError that is NOT in harbor's exclude_exceptions,
        # so the trial burns its retry budget and is recorded as a hard failure
        # instead of the clean exclusion we want. So write NO reward.json and an
        # empty reward.txt — harbor then surfaces RewardFileEmptyError and
        # excludes the trial. Mirrors the other two invalid-trial paths
        # (run_verify._write_runner_failure, test.sh exit 2/3).
        print(
            "  AGGREGATE: no scorable verification result "
            "(verifier crashed/absent, or expected validation produced no results) "
            "— marking trial INVALID (reward=None).",
            file=sys.stderr,
        )
        (LOGS_DIR / "reward.json").unlink(missing_ok=True)
        (LOGS_DIR / "reward.txt").write_text("")
    else:
        # flat reward.json: flat scalars only (harbor-compatible)
        flat = _build_flat_reward(reward, verifier, verifier_total, validation, judge, rubric_all)
        (LOGS_DIR / "reward.json").write_text(json.dumps(flat, indent=2))
        (LOGS_DIR / "reward.txt").write_text(str(int(reward)))


def _build_flat_reward(
    reward: float | None,
    verifier: dict | None,
    verifier_total: int,
    validation: dict | None,
    judge: dict,
    rubric_all: dict | None,
) -> dict[str, float | int | None]:
    """Build the flat reward dict with only scalar values.

    ``reward``/``correctness`` are None when validation was expected but
    crashed (invalid trial); all other keys remain scalar-or-absent.
    """
    flat: dict[str, float | int | None] = {
        "reward": reward,
        "correctness": reward,
    }

    # Verifier score (passed/total ratio)
    verifier_passed = verifier.get("passed", 0) if verifier else 0
    if verifier_total > 0:
        flat["verifier_score"] = round(verifier_passed / verifier_total, 4)

    # Validation score
    if validation is not None:
        val_score = validation.get("validation_score")
        if val_score is not None:
            flat["validation_score"] = round(float(val_score), 4)

    # Rubric scores
    rubric_score = judge.get("rubric_score")
    if rubric_score is not None:
        flat["rubric_score"] = round(float(rubric_score), 4)
    rubric_f2p = judge.get("fail_to_pass_score")
    if rubric_f2p is not None:
        flat["rubric_f2p_score"] = round(float(rubric_f2p), 4)
    rubric_p2p = judge.get("pass_to_pass_score")
    if rubric_p2p is not None:
        flat["rubric_p2p_score"] = round(float(rubric_p2p), 4)

    # Rubric-all scores
    if rubric_all and isinstance(rubric_all, dict):
        ra_score = rubric_all.get("rubric_score")
        if ra_score is not None:
            flat["rubric_all_score"] = round(float(ra_score), 4)
        ra_f2p = rubric_all.get("fail_to_pass_score")
        if ra_f2p is not None:
            flat["rubric_all_f2p_score"] = round(float(ra_f2p), 4)
        ra_p2p = rubric_all.get("pass_to_pass_score")
        if ra_p2p is not None:
            flat["rubric_all_p2p_score"] = round(float(ra_p2p), 4)

    # Taste scores
    patch_bloat = judge.get("patch_bloat")
    if patch_bloat and isinstance(patch_bloat, dict):
        bloat_ratio = patch_bloat.get("bloat_ratio")
        if bloat_ratio is not None:
            flat["taste_patch_bloat"] = round(float(bloat_ratio), 4)
    taste_dict = judge.get("taste")
    if taste_dict and isinstance(taste_dict, dict):
        pa_score = taste_dict.get("practice_alignment_score")
        if pa_score is not None:
            flat["taste_practice_alignment"] = round(float(pa_score), 2)
        rt_score = taste_dict.get("relative_taste_score")
        if rt_score is not None:
            flat["taste_relative_taste"] = round(float(rt_score), 2)

    # Judge ok/fail flags. Numeric so harbor's strict
    # dict[str, float | int] reward schema accepts them. Surfaced only when
    # the judge was attempted (non-empty status) — a task that has no
    # rubric / no taste at all stays absent. Reviewers and the
    # promotion-analysis skill can gate on these to refuse trials with
    # unmeasured rubric/taste signal. Full status string + error text live
    # in reward_details.json for post-mortem.
    for status_key, flag_key in (
        ("rubric_status", "rubric_judge_ok"),
        ("rubric_all_status", "rubric_all_judge_ok"),
        ("taste_status", "taste_judge_ok"),
    ):
        status = judge.get(status_key)
        if status:
            flat[flag_key] = 1 if status == "ok" else 0

    return flat


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "parse-pytest":
            parse_pytest()
        elif cmd == "aggregate":
            aggregate()
        else:
            print(f"Usage: {sys.argv[0]} <parse-pytest|aggregate>", file=sys.stderr)
            sys.exit(1)
    except Exception:
        traceback.print_exc(file=sys.stderr)
