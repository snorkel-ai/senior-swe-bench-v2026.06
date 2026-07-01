"""Unified validation judge.

The judge produces two output channels via the ``submit_review`` tool:

  * ``severity`` — gating-grade record with per-criterion scores 0-3, stays
    orchestrator-side.
  * ``actionable`` — engineering advice surfaced to the validation agent on
    retry. The judge prompt keeps this channel free of the gating taxonomy so
    retry feedback never tells the agent how it was scored.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import jinja2
from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — only exercised on py3.10
    import tomli as tomllib  # type: ignore

from .. import llm_utils
from ..validation_agent import DEFAULT_MANDATE_PATH

# ───────────────────────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
DEFAULT_RUBRIC_PATH = _HERE / "validation_judge_rubric.toml"
_PROMPT_TEMPLATE_PATH = _HERE / "validation_judge_prompt.md.j2"
# DEFAULT_MANDATE_PATH (the validation agent's mandate, embedded below so the
# judge scores against the same rules the agent ran under) is imported from the
# sibling ``validation_agent`` package — its canonical home.

# Per-input character clamps.
MAX_SPEC_CHARS = 60_000
MAX_SCRIPT_CHARS = 8_000  # per script
MAX_AGENT_PATCH_CHARS = 100_000
MAX_EXEC_RESULTS_CHARS = 20_000
MAX_SMOKE_CHARS = 8_000
MAX_DIAGNOSTICS_CHARS = 8_000

_DEFAULT_MAX_OUTPUT_TOKENS = 2500
_THINKING_MAX_OUTPUT_TOKENS = 4000


# Per-story run-log outcome. Subclassing ``str`` keeps the wire values and JSON
# serialization unchanged while letting callers compare against the members.
class FailureClass(str, Enum):
    passed = "pass"  # 'pass' is a reserved keyword, so the member is 'passed'
    script_error = "script_error"
    behavioral_fail = "behavioral_fail"
    unsure = "unsure"


# ───────────────────────────────────────────────────────────────────────────
# Rubric
# ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RubricCriterion:
    id: str
    description: str
    guide: str
    scale: str = "0-3"
    gating: bool = False


@dataclass(frozen=True)
class Rubric:
    judge_model_default: str
    criteria: tuple[RubricCriterion, ...]

    @classmethod
    def load(cls, path: Path) -> Rubric:
        data = tomllib.loads(path.read_text())
        return cls(
            judge_model_default=data["judge_model_default"],
            criteria=tuple(RubricCriterion(**c) for c in data["criterion"]),
        )

    @property
    def gating_ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.criteria if c.gating)


# ───────────────────────────────────────────────────────────────────────────
# Pydantic wire models — also drive Anthropic's tool_use input_schema
# ───────────────────────────────────────────────────────────────────────────


class _StoryJudgementWire(BaseModel):
    implemented: bool
    faithful_score: float = Field(ge=0, le=3)
    failure_class: FailureClass = Field(
        description="Per-story failure class; classify per the per-story breakdown instructions.",
    )
    notes: str


class _SeverityWire(BaseModel):
    """Severity channel: gating-grade scoring."""

    per_criterion: dict[str, int] = Field(
        description="Map of rubric criterion ID → score 0-3.",
    )
    rationales: dict[str, str] = Field(
        description="One-sentence rationale per criterion ID.",
    )
    per_story: dict[str, _StoryJudgementWire] = Field(
        description="Per-story breakdown, keyed by story ID.",
    )
    notes: str = Field(default="", description="Free-form ≤ 2 sentence summary.")


class _ActionableWire(BaseModel):
    suggestion: str
    story_id: str | None = None
    severity_hint: Literal["minor", "moderate"] = "minor"


class _ReviewWire(BaseModel):
    """Top-level wire object the judge emits via the submit_review tool."""

    severity: _SeverityWire
    actionable: list[_ActionableWire] = Field(default_factory=list, max_length=10)


def _tool_schema(rubric: Rubric) -> dict[str, Any]:
    """Build the OpenAI tools[] entry from the pydantic wire model."""
    schema = _ReviewWire.model_json_schema()
    # Annotate per_criterion with the expected criterion IDs so the model has a
    # hard anchor without us hand-rolling a properties dict.
    crit_ids = ", ".join(c.id for c in rubric.criteria)
    schema["$defs"]["_SeverityWire"]["properties"]["per_criterion"]["description"] = (
        f"Map of rubric criterion ID → score 0-3. Required keys: {crit_ids}."
    )
    return llm_utils.openai_tool(
        "submit_review",
        "Submit the validation-judge review with severity scores and actionable retry feedback.",
        schema,
    )


# ───────────────────────────────────────────────────────────────────────────
# Inputs / outputs (orchestrator-side dataclasses)
# ───────────────────────────────────────────────────────────────────────────


@dataclass
class JudgeInputs:
    """Everything the judge needs to score one trial.

    Pre-retry calls populate ``smoke_results`` (and leave
    ``execution_results`` / ``execution_diagnostics`` as ``None``).
    Post-execution calls populate ``execution_results`` +
    ``execution_diagnostics`` and leave ``smoke_results`` as ``None``.
    """

    validation_spec_text: str
    spec_instructions: str
    stories: list[dict[str, Any]]
    generated_scripts: dict[str, str]
    agent_patch: str
    smoke_results: dict[str, Any] | None = None
    execution_results: dict[str, Any] | None = None
    execution_diagnostics: dict[str, Any] | None = None

    @property
    def stage_label(self) -> Literal["pre-retry", "post-exec", "no-execution"]:
        if self.smoke_results is not None and self.execution_results is None:
            return "pre-retry"
        if self.execution_results is not None:
            return "post-exec"
        return "no-execution"


@dataclass(frozen=True)
class StoryJudgement:
    story_id: str
    implemented: bool
    faithful_score: float
    failure_class: FailureClass
    notes: str


@dataclass(frozen=True)
class SeverityFindings:
    """Gating-grade record. Stays orchestrator-side."""

    per_criterion: dict[str, int]
    rationales: dict[str, str]
    per_story: dict[str, StoryJudgement]
    overall: float
    discard_recommended: bool
    discard_reasons: tuple[str, ...]

    def dump_dict(self) -> dict[str, Any]:
        return {
            "per_criterion": dict(self.per_criterion),
            "rationales": dict(self.rationales),
            "per_story": {
                sid: {
                    "implemented": s.implemented,
                    "faithful_score": s.faithful_score,
                    "failure_class": s.failure_class,
                    "notes": s.notes,
                }
                for sid, s in self.per_story.items()
            },
            "overall": self.overall,
            "discard_recommended": self.discard_recommended,
            "discard_reasons": list(self.discard_reasons),
            "any_script_error": self.any_script_error,
            "any_retryable": self.any_retryable,
        }

    def story_ids(self, *classes: FailureClass) -> list[str]:
        """Story IDs whose failure_class is one of ``classes``."""
        return [sid for sid, s in self.per_story.items() if s.failure_class in classes]

    @property
    def any_script_error(self) -> bool:
        """True if the judge confidently classed any story's failure as the
        test's own fault. Drives the script-error discard of a story that did
        not execute."""
        return bool(self.story_ids(FailureClass.script_error))

    @property
    def any_retryable(self) -> bool:
        """True if any story warrants a revision pass — the test's own fault
        (script_error) or an unattributed failure (unsure). Drives the revision
        gate; unsure stories are retried but never discarded on that basis."""
        return bool(self.story_ids(FailureClass.script_error, FailureClass.unsure))


@dataclass(frozen=True)
class ActionableSuggestion:
    """Engineering-advice entry. Visible to the validation agent on retry."""

    suggestion: str
    story_id: str | None = None
    severity_hint: Literal["minor", "moderate"] = "minor"

    def dump_dict(self) -> dict[str, Any]:
        return {
            "suggestion": self.suggestion,
            "story_id": self.story_id,
            "severity_hint": self.severity_hint,
        }


@dataclass(frozen=True)
class JudgeResult:
    severity: SeverityFindings
    actionable: tuple[ActionableSuggestion, ...]
    raw: dict[str, Any]
    cost_input_tokens: int = 0
    cost_output_tokens: int = 0
    elapsed_sec: float = 0.0
    truncation_notes: tuple[str, ...] = field(default_factory=tuple)

    def dump_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.dump_dict(),
            "actionable": [a.dump_dict() for a in self.actionable],
            "cost_input_tokens": self.cost_input_tokens,
            "cost_output_tokens": self.cost_output_tokens,
            "elapsed_sec": self.elapsed_sec,
            "truncation_notes": list(self.truncation_notes),
            "raw": self.raw,
        }


# ───────────────────────────────────────────────────────────────────────────
# Truncation
# ───────────────────────────────────────────────────────────────────────────


def _clamp(text: str, limit: int, label: str) -> tuple[str, str | None]:
    """Head-truncate to ``limit`` chars. Returns ``(text, note_or_None)``."""
    if len(text) <= limit:
        return text, None
    marker = f"\n\n[…{len(text) - limit} chars truncated…]"
    return text[: limit - len(marker)] + marker, f"truncated {label}: {len(text)} → {limit} chars"


def _clamp_json(payload: object | None, limit: int, label: str) -> tuple[str, str | None]:
    if payload is None:
        return "", None
    return _clamp(json.dumps(payload, indent=2), limit, label)


# ───────────────────────────────────────────────────────────────────────────
# Prompt
# ───────────────────────────────────────────────────────────────────────────


def render_judge_prompt(
    inputs: JudgeInputs,
    rubric: Rubric,
) -> tuple[str, tuple[str, ...]]:
    """Build the full judge prompt. Returns ``(prompt_text, truncation_notes)``.

    Truncation notes appear both inside the prompt (so the judge knows
    something was clamped) and on the JudgeResult (for calibration runs).
    """
    notes: list[str] = []

    spec, n = _clamp(inputs.validation_spec_text, MAX_SPEC_CHARS, "validation_spec")
    if n:
        notes.append(n)

    patch, n = _clamp(inputs.agent_patch, MAX_AGENT_PATCH_CHARS, "agent.patch")
    if n:
        notes.append(n)

    scripts: dict[str, str] = {}
    for name, body in inputs.generated_scripts.items():
        body_clamped, n = _clamp(body, MAX_SCRIPT_CHARS, f"script {name}")
        if n:
            notes.append(n)
        scripts[name] = body_clamped

    smoke_json, n = _clamp_json(inputs.smoke_results, MAX_SMOKE_CHARS, "smoke_results")
    if n:
        notes.append(n)
    exec_json, n = _clamp_json(inputs.execution_results, MAX_EXEC_RESULTS_CHARS, "execution_results")
    if n:
        notes.append(n)
    diag_json, n = _clamp_json(inputs.execution_diagnostics, MAX_DIAGNOSTICS_CHARS, "execution_diagnostics")
    if n:
        notes.append(n)

    # The validation agent's mandate, with container-path placeholders replaced
    # by readable descriptions (the judge cares about the rules, not the paths).
    mandate = (
        DEFAULT_MANDATE_PATH.read_text()
        .rstrip("\n")
        .format(
            scripts_dir="its designated scripts directory (and nowhere else)",
            patch_path="the implementation patch under test",
        )
    )

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_PROMPT_TEMPLATE_PATH.parent)),
        keep_trailing_newline=True,
    )
    template = env.get_template(_PROMPT_TEMPLATE_PATH.name)
    prompt = template.render(
        agent_mandate=mandate,
        spec_text=spec,
        agent_patch=patch,
        scripts=scripts,
        smoke_results_json=smoke_json,
        execution_results_json=exec_json,
        execution_diagnostics_json=diag_json,
        criteria=rubric.criteria,
        truncation_notes=notes,
    )
    return prompt, tuple(notes)


# ───────────────────────────────────────────────────────────────────────────
# Response parsing
# ───────────────────────────────────────────────────────────────────────────


def parse_response(tool_input: dict[str, Any]) -> tuple[SeverityFindings, tuple[ActionableSuggestion, ...]]:
    """Validate the model's tool-use payload via ``_ReviewWire`` and convert
    to orchestrator-side dataclasses.

    Pydantic raises ``ValidationError`` on a malformed payload. This function
    is rubric-unaware, so it does NOT fill in criteria the judge omitted; the
    fail-safe for an omitted gating criterion is applied by ``should_discard``.
    """
    review = _ReviewWire.model_validate(tool_input)
    sev = review.severity

    per_criterion = {k: int(v) for k, v in sev.per_criterion.items()}
    overall = round(sum(per_criterion.values()) / len(per_criterion), 3) if per_criterion else 0.0

    per_story = {
        sid: StoryJudgement(
            story_id=sid,
            implemented=s.implemented,
            faithful_score=float(s.faithful_score),
            failure_class=s.failure_class,
            notes=s.notes,
        )
        for sid, s in sev.per_story.items()
    }

    findings = SeverityFindings(
        per_criterion=per_criterion,
        rationales=dict(sev.rationales),
        per_story=per_story,
        overall=overall,
        discard_recommended=False,  # set by should_discard at policy time
        discard_reasons=(),
    )
    actionable = tuple(
        ActionableSuggestion(
            suggestion=a.suggestion.strip(),
            story_id=a.story_id,
            severity_hint=a.severity_hint,
        )
        for a in review.actionable
        if a.suggestion.strip()
    )
    return findings, actionable


# ───────────────────────────────────────────────────────────────────────────
# Discard policy
# ───────────────────────────────────────────────────────────────────────────


def should_discard(
    severity: SeverityFindings,
    rubric: Rubric | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Pure policy: severity-0 on any gating criterion ⇒ discard.

    Returns ``(discard_bool, reasons_tuple)``. Reasons are dimension IDs.
    Caller is responsible for any logging / sys.exit.
    """
    rubric = rubric or _load_default_rubric()
    # Fail-safe: a gating criterion the judge OMITTED from per_criterion counts
    # as 0 (discard), not a perfect score — the tool schema does not mark these
    # keys required, so a dropped key would otherwise silently pass the trial.
    bad = [c.id for c in rubric.criteria if c.gating and severity.per_criterion.get(c.id, 0) == 0]
    return bool(bad), tuple(bad)


# ───────────────────────────────────────────────────────────────────────────
# Top-level judge function
# ───────────────────────────────────────────────────────────────────────────


def judge(
    inputs: JudgeInputs,
    *,
    rubric: Rubric | None = None,
    model: str | None = None,
    thinking_budget: int = 0,
) -> JudgeResult:
    """Score one trial. Single model call, deterministic post-processing.

    Routing is internal to ``llm_utils.complete`` (litellm). Caller policy
    decides what to do with ``result.severity.discard_recommended`` and
    ``result.actionable``.
    """
    rubric = rubric or _load_default_rubric()
    model = model or rubric.judge_model_default

    prompt_text, truncation_notes = render_judge_prompt(inputs, rubric)
    tool = _tool_schema(rubric)
    max_tokens = _THINKING_MAX_OUTPUT_TOKENS if thinking_budget > 0 else _DEFAULT_MAX_OUTPUT_TOKENS

    started = time.monotonic()
    response = llm_utils.complete(
        model=model,
        messages=[{"role": "user", "content": prompt_text}],
        max_tokens=max_tokens,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "submit_review"}},
        thinking_budget=thinking_budget,
    )
    elapsed = time.monotonic() - started

    tool_input = llm_utils.tool_call_args(response, "submit_review")
    if tool_input is None:
        # The judge produced no parseable submit_review call despite tool_choice
        # forcing it. Surface a clear diagnostic instead of letting parse_response
        # raise a bare Pydantic ValidationError on an empty dict.
        raise RuntimeError(
            "Validation judge returned no parseable submit_review tool call "
            "(forced tool_choice not honored or arguments were invalid JSON)."
        )
    severity, actionable = parse_response(tool_input)

    discard, reasons = should_discard(severity, rubric)
    severity = SeverityFindings(
        per_criterion=severity.per_criterion,
        rationales=severity.rationales,
        per_story=severity.per_story,
        overall=severity.overall,
        discard_recommended=discard,
        discard_reasons=reasons,
    )

    usage = getattr(response, "usage", None)
    in_tok = int(getattr(usage, "prompt_tokens", 0)) if usage else 0
    out_tok = int(getattr(usage, "completion_tokens", 0)) if usage else 0

    return JudgeResult(
        severity=severity,
        actionable=actionable,
        raw=tool_input,
        cost_input_tokens=in_tok,
        cost_output_tokens=out_tok,
        elapsed_sec=elapsed,
        truncation_notes=truncation_notes,
    )


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────


def _load_default_rubric() -> Rubric:
    return Rubric.load(DEFAULT_RUBRIC_PATH)
