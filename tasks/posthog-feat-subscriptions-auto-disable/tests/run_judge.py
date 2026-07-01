#!/usr/bin/env python3
# /// script
# dependencies = [
#   "litellm>=1.0",
#   "pydantic>=2.0",
#   "unidiff>=0.7",
#   "pygments>=2.17",
# ]
# ///
"""LLM judges for Harbor benchmark tasks.

Two entry points called by test.sh:
  python3 /tests/run_judge.py rubric  # rubric evaluation → reward.json
  python3 /tests/run_judge.py taste   # taste evaluation → appends to reward.json
"""

import contextlib
import json
import os
import statistics
import sys
from pathlib import Path

JUDGE_OUTPUT = Path("/logs/verifier/judge_output.json")


def _write_judge_status(key: str, status: str, error: str = "") -> None:
    """Merge a status entry into JUDGE_OUTPUT so failures are observable.

    Every silent-skip / failure path in this module calls this before
    exiting so run_aggregate.py and downstream tooling can distinguish
    "judge ran fine but produced null" from "judge never ran" from
    "judge ran and crashed".
    """
    try:
        JUDGE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if JUDGE_OUTPUT.exists():
            with contextlib.suppress(Exception):
                existing = json.loads(JUDGE_OUTPUT.read_text())
        existing[f"{key}_status"] = status
        if error:
            existing[f"{key}_error"] = error[:500]
        JUDGE_OUTPUT.write_text(json.dumps(existing, indent=2))
    except Exception as exc:
        print(f"Failed to write judge status ({key}={status}): {exc}", file=sys.stderr)


try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    print("Missing dependency (pydantic). Skipping judges.", file=sys.stderr)
    _write_judge_status("rubric", "skipped:missing_deps")
    _write_judge_status("taste", "skipped:missing_deps")
    sys.exit(0)

RUBRIC_PATH = Path("/tests/judge/rubric.json")
RUBRIC_ALL_PATH = Path("/tests/judge/rubric_all.json")
RUBRIC_PASS_THRESHOLD = 0.5
_repo_name = os.environ.get("REPO_NAME")
if not _repo_name:
    print("REPO_NAME not set. Skipping LLM judge.", file=sys.stderr)
    _write_judge_status("rubric", "skipped:no_repo_name")
    _write_judge_status("taste", "skipped:no_repo_name")
    sys.exit(0)
REPO_PATH = Path("/repo") / _repo_name


class CriterionScore(BaseModel):
    name: str
    # Binary by mandate: 1.0 = satisfied, 0.0 = not. ``json_schema_extra`` puts
    # the {0, 1} enum into the emitted tool schema so the model is told to score
    # binary; the validator is a defensive backstop that snaps any stray
    # fractional value to the nearest pole (a genuinely-achieved majority rounds
    # up, not down) so judge slips can't silently introduce partial credit.
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Binary only: 1 if the criterion is satisfied, 0 if not. No in-between values.",
        json_schema_extra={"enum": [0, 1]},
    )
    reason: str

    @field_validator("score")
    @classmethod
    def _binarize(cls, v: float) -> float:
        return 1.0 if v >= 0.5 else 0.0


class RubricResult(BaseModel):
    criteria: list[CriterionScore]


def parse_rubric(rubric_path: Path) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Parse rubric.json. Returns (file_paths, fail_to_pass, pass_to_pass)."""
    data = json.loads(rubric_path.read_text())
    files = data.get("files", [])
    fail_to_pass = {c["name"]: c["description"] for c in data.get("fail_to_pass", [])}
    pass_to_pass = {c["name"]: c["description"] for c in data.get("pass_to_pass", [])}
    return files, fail_to_pass, pass_to_pass


def read_file(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError as exc:
        return f"[Error reading file: {exc}]"


RUBRIC_EXPLORE_SYSTEM = (
    "You are evaluating whether an AI agent correctly completed a software "
    "engineering task. You have tools to read files and list directories in "
    "the repository, and submit_scores to record your evaluation.\n\n"
    "The agent may have placed code in different files than "
    "the pre-specified reference paths — use read_file and list_directory to "
    "find and read the actual implementation.\n\n"
    "When you have enough context to score all criteria, call submit_scores."
)


def _build_criteria_block(fail_to_pass: dict[str, str], pass_to_pass: dict[str, str]) -> str:
    """Build the segmented criteria text block for rubric prompts."""
    parts = []
    if fail_to_pass:
        ftp_lines = "\n".join(f"  {i + 1}. [{name}] {desc}" for i, (name, desc) in enumerate(fail_to_pass.items()))
        parts.append(
            "### Fail-to-Pass Criteria\n"
            "These should be FALSE in unmodified code and TRUE only after a correct fix.\n"
            "Score each criterion as a BINARY value — exactly 1 or exactly 0, never an "
            "in-between value.\n"
            "Score 1 when the implementation achieves the substantial majority of what the "
            "criterion requires INCLUDING its core/hard part. Score 0 otherwise.\n\n" + ftp_lines
        )
    if pass_to_pass:
        ptp_lines = "\n".join(f"  {i + 1}. [{name}] {desc}" for i, (name, desc) in enumerate(pass_to_pass.items()))
        parts.append(
            "### Pass-to-Pass Criteria\n"
            "These should remain TRUE regardless of which fix approach was used.\n"
            "They guard against shortcuts that fix the symptom but degrade quality.\n"
            "Score each criterion as a BINARY value — exactly 1 or exactly 0, never an "
            "in-between value. Score 1 if preserved, 0 if violated or degraded.\n\n" + ptp_lines
        )
    return "\n\n".join(parts)


def _rubric_explore_and_score(  # noqa: PLR0913
    model: str,
    criteria_block: str,
    all_names: list[str],
    agent_patch_text: str,
    changed_files: list[str],
    extra_reference_files: list[str],
    repo_name: str,
    error_holder: list[str] | None = None,
) -> RubricResult | None:
    """Run the agentic explore + score flow in a single conversation.

    The judge explores with read_file/list_directory, then scores via
    submit_scores — all in the same message thread (centralized in
    ``llm_tools.run_explore_and_emit``) so file contents read during
    exploration remain in context for scoring.

    Returns parsed RubricResult or None on failure.
    """
    patch_size = len(agent_patch_text.encode("utf-8", errors="replace"))
    if patch_size <= RUBRIC_MAX_PATCH_SIZE:
        patch_section = f"```diff\n{agent_patch_text}\n```"
    else:
        truncated = agent_patch_text[:RUBRIC_MAX_PATCH_SIZE]
        patch_section = (
            f"```diff\n{truncated}\n```\n\n"
            f"... (patch truncated at {RUBRIC_MAX_PATCH_SIZE // 1000}KB, "
            f"{patch_size:,} bytes total. Read changed files directly for full content.)"
        )

    changed_list = "\n".join(f"- {f}" for f in changed_files) or "(none detected)"
    extra_list = "\n".join(f"- {f}" for f in extra_reference_files) if extra_reference_files else "(none)"

    explore_prompt = (
        f"Evaluate the agent's work in the '{repo_name}' codebase against these criteria.\n\n"
        f"## Agent's Patch\n{patch_section}\n\n"
        f"## Files Changed by the Agent\n{changed_list}\n\n"
        f"## Additional Reference Files\n"
        f"These files from the original codebase may contain relevant context:\n{extra_list}\n\n"
        f"## Rubric Criteria\n{criteria_block}\n\n"
        "Read the changed files and any additional files you need to evaluate these criteria.\n"
        "When you have enough context, call submit_scores with your scores.\n\n"
        f"Use exactly these criterion names: {all_names}"
    )

    try:
        return llm_tools.run_explore_and_emit(
            model=model,
            system=RUBRIC_EXPLORE_SYSTEM,
            user=explore_prompt,
            repo_path=REPO_PATH,
            explore_tools=llm_tools.READ_TOOLS,
            emit_tool_name="submit_scores",
            emit_schema=RubricResult,
            max_turns=RUBRIC_MAX_EXPLORE_TURNS,
            max_tokens=2048,
            max_file_lines=RUBRIC_MAX_FILE_LINES,
            label="rubric",
        )
    except Exception as e:  # noqa: BLE001
        print(f"Rubric explore/score error: {e}", file=sys.stderr)
        if error_holder is not None:
            error_holder.append(f"explore/score: {type(e).__name__}: {e}")
        return None


def _rubric_score_single_shot(
    model: str,
    criteria_block: str,
    all_names: list[str],
    files_block: str,
    repo_name: str,
    error_holder: list[str] | None = None,
) -> RubricResult | None:
    """Single-shot scoring (no exploration). Used for nop/empty-patch fallback."""
    prompt = (
        f"You are evaluating whether an AI agent correctly completed a "
        f"software engineering task in the '{repo_name}' codebase.\n\n"
        "## Repository files\n\n"
        f"{files_block}\n\n"
        "## Rubric\n\n"
        f"{criteria_block}\n\n"
        f"Use exactly these criterion names: {all_names}"
    )
    try:
        return llm_utils.complete_structured(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            schema=RubricResult,
            tool_name="submit_scores",
            tool_description="Submit rubric evaluation scores for each criterion.",
        )
    except Exception as e:  # noqa: BLE001
        print(f"Rubric single-shot API error: {e}", file=sys.stderr)
        if error_holder is not None:
            error_holder.append(f"single-shot: {type(e).__name__}: {e}")
        return None


def rubric_main(rubric_path: Path | None = None, output_key: str = "rubric") -> None:  # noqa: PLR0915
    """Run the LLM rubric judge.

    Uses an agentic explore loop when an agent patch is available (the agent
    may have placed code in different files than the rubric's pre-specified
    paths). Falls back to single-shot scoring for nop/empty-patch runs.

    Args:
        rubric_path: Path to the rubric JSON file. Defaults to RUBRIC_PATH.
        output_key: Key in reward.json to write results under. "rubric" for
            the standard rubric, "rubric_all" for the full test-criteria rubric.
    """
    rubric_path = rubric_path or RUBRIC_PATH
    if not llm_utils.have_credentials():
        print(
            "No LLM credentials (PORTKEY_API_KEY / ANTHROPIC_API_KEY). Skipping LLM judge.",
            file=sys.stderr,
        )
        _write_judge_status(output_key, "skipped:no_api_key")
        sys.exit(0)

    if not rubric_path.exists():
        print(f"{rubric_path} not found. Skipping LLM judge.", file=sys.stderr)
        _write_judge_status(output_key, "skipped:no_rubric_file")
        sys.exit(0)

    file_paths, fail_to_pass, pass_to_pass = parse_rubric(rubric_path)
    all_criteria = {**fail_to_pass, **pass_to_pass}
    if not all_criteria:
        print("No criteria in rubric.json. Skipping LLM judge.", file=sys.stderr)
        _write_judge_status(output_key, "skipped:no_criteria")
        sys.exit(0)

    criteria_block = _build_criteria_block(fail_to_pass, pass_to_pass)
    all_names = list(all_criteria.keys())
    repo_name = os.environ["REPO_NAME"]
    model = llm_utils.judge_model()

    # Read agent patch to decide agentic vs single-shot
    agent_patch_path = Path("/logs/verifier/agent.patch")
    agent_patch_text = ""
    if agent_patch_path.exists():
        agent_patch_text = agent_patch_path.read_text(errors="replace").strip()

    errors: list[str] = []
    if agent_patch_text:
        # Agentic flow: explore + score in single conversation
        changed_files = _extract_changed_files(agent_patch_text)
        changed_set = set(changed_files)
        extra_reference_files = [p for p in file_paths if p not in changed_set]

        print(
            f"Rubric judge (agentic): {len(changed_files)} changed files, "
            f"{len(extra_reference_files)} extra reference files",
            flush=True,
        )

        result = _rubric_explore_and_score(
            model,
            criteria_block,
            all_names,
            agent_patch_text,
            changed_files,
            extra_reference_files,
            repo_name,
            error_holder=errors,
        )
    else:
        # Nop / empty-patch fallback: single-shot with pre-specified files
        print("Rubric judge (single-shot): no agent patch, using pre-specified files", flush=True)
        file_sections = []
        for rel_path in file_paths:
            content = read_file(REPO_PATH / rel_path)
            file_sections.append(f"### {rel_path}\n```\n{content}\n```")
        files_block = "\n\n".join(file_sections) or "(no files specified)"

        result = _rubric_score_single_shot(
            model, criteria_block, all_names, files_block, repo_name, error_holder=errors
        )

    if result is None:
        if errors:
            print(f"LLM judge failed: {errors[0]}", file=sys.stderr)
            _write_judge_status(output_key, "failed:api_error", "; ".join(errors))
        else:
            print("LLM judge: no tool_use block in response. Skipping.", file=sys.stderr)
            _write_judge_status(output_key, "failed:no_tool_use")
        sys.exit(0)

    scores = {c.name: c.score for c in result.criteria}

    # Compute segmented scores
    ftp_scores = []
    for n in fail_to_pass:
        s = scores.get(n)
        if s is not None:
            ftp_scores.append(s)
        else:
            print(f"  WARNING: criterion '{n}' not returned by judge, skipping from average", file=sys.stderr)

    ptp_scores = []
    for n in pass_to_pass:
        s = scores.get(n)
        if s is not None:
            ptp_scores.append(s)
        else:
            print(f"  WARNING: criterion '{n}' not returned by judge, skipping from average", file=sys.stderr)

    fail_to_pass_score = sum(ftp_scores) / len(ftp_scores) if ftp_scores else None
    pass_to_pass_score = sum(ptp_scores) / len(ptp_scores) if ptp_scores else None
    all_scores = list(scores.values())
    rubric_score = sum(all_scores) / len(all_scores) if all_scores else None

    reward_data: dict = {
        "rubric_score": round(rubric_score, 4) if rubric_score is not None else None,
        "fail_to_pass_score": round(fail_to_pass_score, 4) if fail_to_pass_score is not None else None,
        "pass_to_pass_score": round(pass_to_pass_score, 4) if pass_to_pass_score is not None else None,
    }
    for name in all_names:
        s = scores.get(name)
        reward_data[name] = s if s is not None else None

    JUDGE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if JUDGE_OUTPUT.exists():
        with contextlib.suppress(Exception):
            existing = json.loads(JUDGE_OUTPUT.read_text())
    if output_key == "rubric":
        existing.update(reward_data)
    else:
        existing[output_key] = reward_data
    existing[f"{output_key}_status"] = "ok"
    existing.pop(f"{output_key}_error", None)
    JUDGE_OUTPUT.write_text(json.dumps(existing, indent=2))
    ftp_str = f"{fail_to_pass_score:.2f}" if fail_to_pass_score is not None else "n/a"
    ptp_str = f"{pass_to_pass_score:.2f}" if pass_to_pass_score is not None else "n/a"
    rub_str = f"{rubric_score:.2f}" if rubric_score is not None else "n/a"
    print(
        f"LLM judge complete. "
        f"fail_to_pass={ftp_str} "
        f"pass_to_pass={ptp_str} "
        f"rubric={rub_str} "
        f"({len(all_names)} criteria)"
    )
    for c in result.criteria:
        segment = "F" if c.name in fail_to_pass else "P"
        mark = "+" if c.score >= RUBRIC_PASS_THRESHOLD else "-"
        print(f"  [{segment}{mark}] {c.name}: {c.reason}")


# ---------------------------------------------------------------------------
# Taste evaluation
# ---------------------------------------------------------------------------

AGENT_PATCH = Path("/logs/verifier/agent.patch")

RUBRIC_MAX_EXPLORE_TURNS = 10
RUBRIC_MAX_FILE_LINES = 1000
RUBRIC_MAX_PATCH_SIZE = 50_000
# Oracle patch: prefer /tests/ (available for all runs) over /solution/ (oracle-only)
_ORACLE_IN_TESTS = Path("/tests/judge/oracle.patch")
_ORACLE_IN_SOLUTION = Path("/solution/oracle.patch")
ORACLE_PATCH = _ORACLE_IN_TESTS if _ORACLE_IN_TESTS.exists() else _ORACLE_IN_SOLUTION

MAX_EXPLORE_TURNS = 15
# Floor: the taste judge must explore at least this many turns before its score
# is accepted, so it can't grade blind off the two diffs alone.
TASTE_MIN_EXPLORE_TURNS = 3
# Per-patch char cap in the taste prompt. Past this the diff is truncated WITH a
# notice (so the model knows to read changed files directly, rather than scoring
# a silently-cut diff).
TASTE_MAX_PATCH_SIZE = 12_000


# ---------------------------------------------------------------------------
# Pydantic models for structured output
# ---------------------------------------------------------------------------


class DimensionScore(BaseModel):
    """A single taste dimension score with rationale."""

    score: int = Field(..., ge=1, le=5, description="Score from 1 (worst) to 5 (best)")
    rationale: str = Field(..., description="1-2 sentence justification for the score")


class PracticeAlignment(BaseModel):
    """Practice-alignment scores. The dimensions' meaning + per-grade anchors are
    the rubric in ssb_lib/taste_judge/taste_judge_prompt.md (the field names match
    its ``### headings``); no per-field descriptions needed here."""

    style_consistency: DimensionScore
    pattern_adherence: DimensionScore
    library_usage: DimensionScore
    abstraction_level: DimensionScore
    documentation_fit: DimensionScore


class RelativeTaste(BaseModel):
    """Relative-taste scores (vs the reference patch). Dimension meaning + anchors
    live in ssb_lib/taste_judge/taste_judge_prompt.md; field names match its
    ``### headings``."""

    minimality: DimensionScore
    approach_quality: DimensionScore
    hygiene: DimensionScore
    fluency: DimensionScore
    craftsmanship: DimensionScore


class TasteScores(BaseModel):
    """Full taste evaluation with practice alignment and relative taste dimensions."""

    practice_alignment: PracticeAlignment
    relative_taste: RelativeTaste


# ---------------------------------------------------------------------------
# Patch SLOC computation — imported from patch_sloc.py (canonical implementation)
# ---------------------------------------------------------------------------

from ssb_lib import (  # noqa: E402  — shared lib (tests/ssb_lib, /tests on sys.path)
    llm_tools,
    llm_utils,
    taste_judge,  # noqa: E402  — package dir holds taste_judge_prompt.md
)
from ssb_lib.patch_classify import (  # noqa: E402
    FileClassification,
    classify_patch,
    dropped_paths,
    filter_patch_to_behavioral,
)
from ssb_lib.patch_sloc import compute_patch_sloc  # noqa: E402

# The taste judge's single system prompt (exploration guidance + rubric), read
# from the markdown asset in the taste_judge package.
TASTE_SYSTEM_PROMPT = (Path(taste_judge.__file__).parent / "taste_judge_prompt.md").read_text().strip()

# Filtered patches written next to AGENT_PATCH so analyze.py / explorer can
# read them the same way they already read agent.patch.
AGENT_PATCH_FILTERED = Path("/logs/verifier/agent_filtered.patch")
ORACLE_PATCH_FILTERED = Path("/logs/verifier/oracle_filtered.patch")


# ---------------------------------------------------------------------------
# Patch filtering — symmetric path-aware classifier for both patches.
# Filters out test, doc, and formatting files so the SLOC and minimality
# comparisons against the hand-curated oracle are apples-to-apples. See
# patch_classify.py for the full rationale.
# ---------------------------------------------------------------------------


def _classifications_to_dicts(cls: list[FileClassification]) -> list[dict]:
    return [c.model_dump(mode="json") for c in cls]


def _prepare_filtered_patches(
    agent_text: str,
    oracle_text: str,
    *,
    classifier_model: str,
) -> dict:
    """Classify both patches and return filtered text + audit metadata."""
    agent_cls = classify_patch(agent_text, model=classifier_model) if agent_text.strip() else []
    oracle_cls = classify_patch(oracle_text, model=classifier_model) if oracle_text.strip() else []
    return {
        "agent_filtered": filter_patch_to_behavioral(agent_text, agent_cls),
        "oracle_filtered": filter_patch_to_behavioral(oracle_text, oracle_cls),
        "agent_classifications": agent_cls,
        "oracle_classifications": oracle_cls,
        "agent_dropped": dropped_paths(agent_cls),
        "oracle_dropped": dropped_paths(oracle_cls),
    }


# ---------------------------------------------------------------------------
# Assessment 1: Patch Bloat (procedural)
# ---------------------------------------------------------------------------


def assess_patch_bloat(
    agent_text: str,
    oracle_text: str,
    *,
    agent_filtered: str,
    oracle_filtered: str,
    agent_dropped: list[str],
    oracle_dropped: list[str],
) -> dict | None:
    """Compare agent patch SLOC to oracle patch SLOC. No LLM needed.

    Reports both the filtered numbers (authoritative — comparison is fair
    because both sides are reduced to BEHAVIORAL files) and the unfiltered
    numbers (auxiliary signal; useful for spotting classifier regressions).
    """
    if not agent_text.strip() or not oracle_text.strip():
        return None

    agent_stats_unf = compute_patch_sloc(agent_text)
    oracle_stats_unf = compute_patch_sloc(oracle_text)
    agent_stats = compute_patch_sloc(agent_filtered)
    oracle_stats = compute_patch_sloc(oracle_filtered)

    # A patch that failed to parse is UNMEASURED, not a perfect 0-SLOC minimal
    # change — don't emit a (misleadingly excellent) bloat ratio for it. (H11)
    if any(s.get("parse_error") for s in (agent_stats_unf, oracle_stats_unf, agent_stats, oracle_stats)):
        return None

    if oracle_stats["sloc"] == 0:
        # Filtered oracle empty (very small or fully-dropped patch). Fall back
        # to the unfiltered ratio so we still report something useful.
        if oracle_stats_unf["sloc"] == 0:
            return None
        ratio = round(agent_stats_unf["sloc"] / oracle_stats_unf["sloc"], 3)
        ratio_unf = ratio
    else:
        ratio = round(agent_stats["sloc"] / oracle_stats["sloc"], 3)
        ratio_unf = (
            round(agent_stats_unf["sloc"] / oracle_stats_unf["sloc"], 3) if oracle_stats_unf["sloc"] > 0 else None
        )

    return {
        "agent_sloc": agent_stats["sloc"],
        "agent_files": agent_stats["files"],
        "agent_hunks": agent_stats["hunks"],
        "oracle_sloc": oracle_stats["sloc"],
        "oracle_files": oracle_stats["files"],
        "oracle_hunks": oracle_stats["hunks"],
        "bloat_ratio": ratio,
        "agent_sloc_unfiltered": agent_stats_unf["sloc"],
        "agent_files_unfiltered": agent_stats_unf["files"],
        "oracle_sloc_unfiltered": oracle_stats_unf["sloc"],
        "oracle_files_unfiltered": oracle_stats_unf["files"],
        "bloat_ratio_unfiltered": ratio_unf,
        "agent_files_dropped": agent_dropped,
        "oracle_files_dropped": oracle_dropped,
    }


# ---------------------------------------------------------------------------
# Assessment 2 & 3: Agent-judged taste (single-thread explore + score)
# ---------------------------------------------------------------------------
# The taste prompt (exploration guidance + the ten-dimension rubric with
# per-grade anchors) lives in ssb_lib/taste_judge/taste_judge_prompt.md, read
# above into TASTE_SYSTEM_PROMPT.


def _extract_changed_files(patch_text: str) -> list[str]:
    """Extract file paths from a unified diff."""
    paths = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[6:])
        elif line.startswith("+++ ") and "/dev/null" not in line:
            paths.append(line[4:].strip())
    return paths


def _taste_patch_section(patch: str) -> str:
    """Fenced diff for the taste prompt, with a truncation notice past the cap."""
    if len(patch) <= TASTE_MAX_PATCH_SIZE:
        return f"```diff\n{patch}\n```"
    return (
        f"```diff\n{patch[:TASTE_MAX_PATCH_SIZE]}\n```\n"
        f"... (patch truncated at {TASTE_MAX_PATCH_SIZE // 1000}K chars, "
        f"{len(patch):,} total. Read the changed files directly for full content.)"
    )


def assess_taste_with_llm(
    agent_patch: str,
    oracle_patch: str,
    *,
    model: str,
) -> dict | None:
    """Single-thread taste evaluation: explore the codebase, then submit scores.

    The judge explores with read-only tools and ends by calling
    ``submit_taste_scores`` — all in one ``llm_tools.run_explore_and_emit``
    thread, so every file it reads stays in context when it scores (no
    summarize-then-rescore handoff). The patches passed here are already
    filtered to behavioral files only (see _prepare_filtered_patches), keeping
    the minimality / craftsmanship judgments scoped to the reference's surface.
    """
    changed_files = _extract_changed_files(agent_patch)
    files_list = ", ".join(changed_files[:10]) if changed_files else "(unknown)"

    user = (
        f"Evaluate this agent's patch against the codebase's practices and the reference patch.\n\n"
        f"## Agent's Patch\n{_taste_patch_section(agent_patch)}\n\n"
        f"## Reference Patch (expert human baseline)\n{_taste_patch_section(oracle_patch)}\n\n"
        f"Files modified: {files_list}\n\n"
        f"Explore the codebase around the changed files to understand its conventions, "
        f"then call submit_taste_scores with all ten dimensions."
    )

    try:
        scored = llm_tools.run_explore_and_emit(
            model=model,
            system=TASTE_SYSTEM_PROMPT,
            user=user,
            repo_path=REPO_PATH,
            explore_tools=llm_tools.EXPLORE_TOOLS,
            emit_tool_name="submit_taste_scores",
            emit_schema=TasteScores,
            max_turns=MAX_EXPLORE_TURNS,
            max_tokens=4096,
            min_explore_turns=TASTE_MIN_EXPLORE_TURNS,
            label="taste",
        )
    except Exception as e:  # noqa: BLE001
        print(f"Taste evaluation API error: {e}", file=sys.stderr)
        return None

    if scored is None:
        print("Taste scoring: no parsed output", file=sys.stderr)
        return None

    return _flatten_taste_scores(scored)


def _flatten_taste_scores(scores: TasteScores) -> dict:
    """Flatten validated TasteScores into a result dict."""
    pa = scores.practice_alignment
    rt = scores.relative_taste

    pa_dims = {
        "style_consistency": pa.style_consistency,
        "pattern_adherence": pa.pattern_adherence,
        "library_usage": pa.library_usage,
        "abstraction_level": pa.abstraction_level,
        "documentation_fit": pa.documentation_fit,
    }
    rt_dims = {
        "minimality": rt.minimality,
        "approach_quality": rt.approach_quality,
        "hygiene": rt.hygiene,
        "fluency": rt.fluency,
        "craftsmanship": rt.craftsmanship,
    }

    # Aggregate the per-dimension scores by MEDIAN (not mean): the median is
    # robust to a single outlier dimension (one brilliant or one weak axis can't
    # swing the headline score), which is the behaviour we want for the
    # taste/practice signals. Each dimension is an integer 0-5, so the median is
    # itself an integer or x.5. This is the single source of truth for the
    # aggregate; explorer.py recomputes the same median from the persisted
    # per-dimension scores for already-collected trials.
    pa_score = round(statistics.median([d.score for d in pa_dims.values()]), 2)
    rt_score = round(statistics.median([d.score for d in rt_dims.values()]), 2)

    result = {
        "practice_alignment_score": pa_score,
        "relative_taste_score": rt_score,
    }
    for k, d in pa_dims.items():
        result[f"pa_{k}"] = d.score
        result[f"pa_{k}_rationale"] = d.rationale
    for k, d in rt_dims.items():
        result[f"rt_{k}"] = d.score
        result[f"rt_{k}_rationale"] = d.rationale

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def taste_main():
    existing = {}
    if JUDGE_OUTPUT.exists():
        with contextlib.suppress(Exception):
            existing = json.loads(JUDGE_OUTPUT.read_text())

    agent_patch = AGENT_PATCH.read_text(errors="replace") if AGENT_PATCH.exists() else ""
    oracle_patch = ORACLE_PATCH.read_text(errors="replace") if ORACLE_PATCH.exists() else ""

    if not (agent_patch.strip() or oracle_patch.strip()):
        _write_judge_status("taste", "skipped:no_patches")
        print("Taste skipped: neither agent nor oracle patch present.", file=sys.stderr)
        return

    # Resolve models once; routing/credentials are internal to llm_utils.
    if llm_utils.have_credentials():
        has_llm = True
        taste_model = llm_utils.judge_model()
        prepared = _prepare_filtered_patches(agent_patch, oracle_patch, classifier_model=llm_utils.classifier_model())
        agent_filtered = prepared["agent_filtered"]
        oracle_filtered = prepared["oracle_filtered"]
        agent_cls = prepared["agent_classifications"]
        oracle_cls = prepared["oracle_classifications"]
        agent_dropped = prepared["agent_dropped"]
        oracle_dropped = prepared["oracle_dropped"]

        # Persist filtered patches next to the raw ones so analyze.py and the
        # explorer can pick them up the same way they read agent.patch today.
        try:
            AGENT_PATCH_FILTERED.parent.mkdir(parents=True, exist_ok=True)
            AGENT_PATCH_FILTERED.write_text(agent_filtered)
            ORACLE_PATCH_FILTERED.write_text(oracle_filtered)
        except OSError as exc:
            print(f"Failed to write filtered patches: {exc}", file=sys.stderr)

        existing["patch_classifications"] = {
            "agent": _classifications_to_dicts(agent_cls),
            "oracle": _classifications_to_dicts(oracle_cls),
        }
    else:
        # No LLM credentials — skip filtering. Bloat ratio falls back to
        # unfiltered numbers, taste judgment is skipped entirely.
        has_llm = False
        taste_model = ""
        agent_filtered = agent_patch
        oracle_filtered = oracle_patch
        agent_dropped = []
        oracle_dropped = []
        existing["taste_status"] = "skipped:no_api_key"
        print("Patch classifier skipped (no API credentials); using unfiltered patches.", file=sys.stderr)

    # Assessment 1: Patch bloat (procedural)
    bloat = assess_patch_bloat(
        agent_patch,
        oracle_patch,
        agent_filtered=agent_filtered,
        oracle_filtered=oracle_filtered,
        agent_dropped=agent_dropped,
        oracle_dropped=oracle_dropped,
    )
    if bloat:
        existing["patch_bloat"] = bloat
        unf = bloat.get("bloat_ratio_unfiltered")
        unf_str = f" (unfiltered ratio={unf})" if unf is not None and unf != bloat["bloat_ratio"] else ""
        dropped_count = len(bloat.get("agent_files_dropped", []))
        dropped_str = f", {dropped_count} agent files dropped" if dropped_count else ""
        print(
            f"Patch bloat: agent={bloat['agent_sloc']} SLOC / oracle={bloat['oracle_sloc']} SLOC "
            f"→ ratio={bloat['bloat_ratio']}{unf_str}{dropped_str}"
        )

    # Assessments 2 & 3: Agent-judged taste (explores codebase autonomously)
    if has_llm and agent_filtered.strip() and oracle_filtered.strip():
        taste = assess_taste_with_llm(agent_filtered, oracle_filtered, model=taste_model)
        if taste:
            existing["taste"] = {k: v for k, v in taste.items() if not k.endswith("_rationale")}
            existing["taste_rationales"] = {k: v for k, v in taste.items() if k.endswith("_rationale")}
            existing["taste_status"] = "ok"
            existing.pop("taste_error", None)
            print(
                f"Taste: practice_alignment={taste.get('practice_alignment_score', 'N/A')}/5 "
                f"relative_taste={taste.get('relative_taste_score', 'N/A')}/5"
            )
            for prefix, label in [("pa_", "Practice"), ("rt_", "Taste")]:
                for k, v in taste.items():
                    if (
                        k.startswith(prefix)
                        and not k.endswith("_rationale")
                        and k not in ("practice_alignment_score", "relative_taste_score")
                    ):
                        dim_name = k[len(prefix) :]
                        rationale = taste.get(f"{k}_rationale", "")
                        print(f"  [{label}] {dim_name}={v}/5: {rationale}")
        else:
            existing["taste_status"] = "failed:api_error"
    elif not has_llm and "taste_status" not in existing:
        existing["taste_status"] = "skipped:no_api_key"
    elif has_llm and not (agent_filtered.strip() and oracle_filtered.strip()):
        existing["taste_status"] = "skipped:empty_patches"

    JUDGE_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JUDGE_OUTPUT.write_text(json.dumps(existing, indent=2))
    print("Taste evaluation complete.")


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    _STATUS_KEY = {"rubric": "rubric", "rubric-all": "rubric_all", "taste": "taste"}.get(cmd)
    try:
        if cmd == "rubric":
            rubric_main()
        elif cmd == "rubric-all":
            rubric_main(rubric_path=RUBRIC_ALL_PATH, output_key="rubric_all")
        elif cmd == "taste":
            taste_main()
        else:
            print(f"Usage: {sys.argv[0]} <rubric|rubric-all|taste>", file=sys.stderr)
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        import traceback as _tb

        print(f"Judge failed: {exc}", file=sys.stderr)
        _tb.print_exc(file=sys.stderr)
        if _STATUS_KEY:
            _write_judge_status(_STATUS_KEY, "failed:uncaught_exception", f"{type(exc).__name__}: {exc}")
        sys.exit(0)
