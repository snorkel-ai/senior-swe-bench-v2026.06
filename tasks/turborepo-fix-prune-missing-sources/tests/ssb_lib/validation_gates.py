"""Pure decision helpers for the validation reward gates.

Separated from ``run_validate`` so the discard and infra-forgiveness decisions
can be exercised against injected execution evidence in isolation.
"""

from __future__ import annotations

from typing import Any

from .drivers.base import Execution
from .validation_judge.judge import FailureClass


def script_error_unexecuted(per_story: dict[str, Any], execution_by_story: dict[str, str]) -> list[str]:
    """Story IDs the judge confidently called a script error AND the runner is
    known not to have executed. Only these are discarded — an executed or
    unattributable run still carries a correctness signal worth scoring."""
    return [
        sid
        for sid, s in per_story.items()
        if s.failure_class == FailureClass.script_error and execution_by_story.get(sid) == Execution.no.value
    ]


def infra_failure_forgivable(
    results: list[dict],
    passed_cases: int,
    total_cases: int,
    per_story: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Whether a non-Submitted validation-agent exit may still record its score.
    The signal is trustworthy when every case passed, when every story is known to
    have run, or when a single story ran and the judge confidently attributed its
    failure to the solution — one executed behavioral failure proves the patch
    wrong regardless of the messy exit or stories that could not run. An
    unattributable or absent run otherwise leaves the score in doubt. Returns
    ``(forgivable, reason)``."""
    if total_cases > 0 and passed_cases == total_cases:
        return True, "all cases passed"
    if results and all(r["execution"] == Execution.yes.value for r in results):
        return True, "every story ran"
    per_story = per_story or {}
    executed_fail = next(
        (
            r["story_id"]
            for r in results
            if r["execution"] == Execution.yes.value
            and getattr(per_story.get(r["story_id"]), "failure_class", None) == FailureClass.behavioral_fail
        ),
        None,
    )
    if executed_fail:
        return True, f"{executed_fail} executed and failed behaviorally"
    return False, None
