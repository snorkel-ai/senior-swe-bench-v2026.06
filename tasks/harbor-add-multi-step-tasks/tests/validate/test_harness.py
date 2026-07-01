"""Test harness for the harbor multi-step trials feature.

Drives the multi-step trial engine end-to-end through the pre-existing public
surface (``Trial.create()`` / ``Trial.run()``) against a task directory the
story materialises, and returns the observable outcomes: the ``TrialResult``
(per-step results, aggregated trial reward, recorded exceptions) plus the
``trial_dir`` path. The story inspects ``trial_dir`` itself to check the on-disk
layout the implementation produced (per-step output isolation under the
discovered container, and trial-root cleanup) — the harness pins no directory
name.

It does NOT author ``task.toml``. The multi-step config shape (the steps
array-of-tables, the per-step ``agent`` / ``verifier`` sub-config, the
reward-strategy field, the per-step abort-threshold field) is the thing under
test, so the story discovers the field names from the implementing source and
writes the config itself. The harness owns only:

  * the two pre-existing factory seams that would otherwise spin up a Docker
    container and a real agent, the ONLY mocks:
      - ``harbor.trial.trial.EnvironmentFactory.create_environment_from_config``
      - ``harbor.trial.trial.AgentFactory.create_agent_from_config``
    The mock environment simulates the verifier writing a reward file: when the
    verifier runs ``test.sh`` (the command carrying the ``2>&1`` redirect) the
    mock writes that step's reward into the trial verifier dir. The mock agent
    records the per-step instruction it was driven with and the effective user
    on the environment at run time, and optionally raises to simulate a fatal
    step.
  * the contract-fixed on-disk scaffold (``write_task_scaffold``): the per-step
    ``steps/{name}/instruction.md`` + ``steps/{name}/tests/test.sh`` and the
    ``environment/Dockerfile``. The engine reads a step's instruction from
    ``steps/{name}/instruction.md`` and its tests from ``steps/{name}/tests/``.
  * reading the result back through the discovered ``result_shape`` (never
    hardcoded), so any valid result-model shape works.

Loads only pre-existing, public harbor modules (``Trial``, ``TrialConfig``,
``ExecResult``, ``AgentInfo``); references no feature-added private symbol.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

ENV_FACTORY = "harbor.trial.trial.EnvironmentFactory.create_environment_from_config"
AGENT_FACTORY = "harbor.trial.trial.AgentFactory.create_agent_from_config"

DOCKERFILE = "FROM ubuntu:24.04\nWORKDIR /app\n"
STEP_TEST_SH = "#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n"

RESULT_SHAPE_KEYS = ("step_results_attr", "step_name_attr", "step_rewards_path", "step_exception_attr")


def write_task_scaffold(task_dir: str | Path, step_names: list[str]) -> None:
    """Write the contract-fixed on-disk scaffold for a task.

    The ``environment/Dockerfile`` and, for each step, the on-disk
    ``steps/{name}/instruction.md`` + ``steps/{name}/tests/test.sh``: the engine
    reads each step's instruction from ``steps/{name}/instruction.md`` and its
    tests from ``steps/{name}/tests/``. Each step's instruction contains the step
    name so the story can assert per-step instruction routing.

    The story authors ``task.toml`` itself, using the field names it discovers
    from the implementing source.
    """
    task_dir = Path(task_dir)
    env_dir = task_dir / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "Dockerfile").write_text(DOCKERFILE)
    for name in step_names:
        step_dir = task_dir / "steps" / name
        tests_dir = step_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / "instruction.md").write_text(f"Do {name}.\n")
        (tests_dir / "test.sh").write_text(STEP_TEST_SH)


def _make_agent() -> MagicMock:
    from harbor.models.trial.result import AgentInfo

    agent = MagicMock()
    agent.name.return_value = "mock-agent"
    agent.version.return_value = "1.0"
    agent.setup = AsyncMock()
    agent.run = AsyncMock()
    agent.to_agent_info.return_value = AgentInfo(name="mock-agent", version="1.0")
    agent.SUPPORTS_ATIF = False
    return agent


def _make_environment(*, is_mounted: bool) -> AsyncMock:
    from harbor.environments.base import ExecResult

    env = AsyncMock()
    env.is_mounted = is_mounted
    env.exec.return_value = ExecResult(stdout="/app\n", stderr="", return_code=0)
    env.upload_dir.return_value = None
    env.upload_file.return_value = None
    env.start.return_value = None
    env.stop.return_value = None
    return env


def _inject_reward(verifier_dir: Path, rewards: dict[str, float] | None) -> None:
    """Simulate the verifier writing a reward file for one step.

    A single ``{"reward": x}`` writes the conventional ``reward.txt``; any
    other shape writes ``reward.json``. Stale files are cleared first so a
    later step never reads an earlier step's reward.
    """
    verifier_dir.mkdir(parents=True, exist_ok=True)
    (verifier_dir / "reward.txt").unlink(missing_ok=True)
    (verifier_dir / "reward.json").unlink(missing_ok=True)
    if rewards is None:
        return
    if set(rewards.keys()) == {"reward"}:
        (verifier_dir / "reward.txt").write_text(str(rewards["reward"]))
    else:
        (verifier_dir / "reward.json").write_text(json.dumps(rewards))
    (verifier_dir / "test-stdout.txt").write_text("PASS\n")


def _step_name_from_target(target_path: str | Path) -> str | None:
    parts = Path(target_path).parts
    if "steps" in parts:
        idx = parts.index("steps")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


def _path_get(obj: Any, dotted: str) -> Any:
    """Resolve a dotted attribute path, returning None if any segment is None."""
    cur = obj
    for part in dotted.split("."):
        if cur is None:
            return None
        cur = getattr(cur, part)
    return cur


def run_trial(
    task_dir: str | Path,
    *,
    runtime: list[dict[str, Any]],
    result_shape: dict[str, str] | None = None,
    is_mounted: bool = True,
    verifier_disabled: bool = False,
) -> dict[str, Any]:
    """Run the multi-step trial engine end-to-end against an authored task dir.

    ``task_dir`` is a directory the story has fully materialised (the authored
    ``task.toml`` plus the scaffold from ``write_task_scaffold``). The harness
    drives ``Trial.create()`` + ``Trial.run()`` against it with the mocked
    environment + mock agent.

    ``runtime`` is the per-step RUNTIME behaviour the mocks simulate, one dict
    per declared step IN THE SAME ORDER as the steps were declared in
    ``task.toml``:
      * ``rewards``      — the reward dict the verifier produces for the step
                           (``None`` / omitted → no reward file written).
      * ``agent_raises`` — if true, the step's agent run raises (a fatal step).
    These are the test scenario's runtime inputs, NOT config — they are not part
    of the config shape under test, so they are passed here rather than written
    into ``task.toml``.

    ``result_shape`` (REQUIRED) tells the harness how to read the multi-step
    result model, whose attribute names are an implementation choice. Discover
    them by reading the implementing source (``src/harbor/models/trial/result.py``)
    and pass a dict with these keys:
      * ``step_results_attr``  — attribute on the ``TrialResult`` holding the
                                 ordered per-step results (one per executed step).
      * ``step_name_attr``     — attribute on each per-step result carrying the
                                 step's name.
      * ``step_rewards_path``  — dotted path on each per-step result to that
                                 step's reward dict (``None`` if the step has no
                                 verifier result), e.g. ``"verifier_result.rewards"``.
      * ``step_exception_attr``— attribute on each per-step result that is
                                 non-None when that step recorded an exception.
    There is deliberately no default: a hardcoded set of names would silently
    work for one implementation and ``AttributeError`` on another, failing the
    story for the wrong reason.

    Returns a JSON-able dict (see the keys assembled at the bottom). It includes
    ``trial_dir`` (a path string) so the story can inspect the on-disk layout
    the implementation produced — the per-step output dirs under the container it
    discovers, and any trial-root mount-dir cleanup — without the harness pinning
    a directory name.
    """
    if result_shape is None or any(k not in result_shape for k in RESULT_SHAPE_KEYS):
        raise ValueError(
            "result_shape is required and must provide all of "
            f"{RESULT_SHAPE_KEYS}. Discover the multi-step result model's "
            "attribute names by reading src/harbor/models/trial/result.py and "
            "pass them as result_shape."
        )
    if runtime is None or not isinstance(runtime, list):
        raise ValueError(
            "runtime is required: a list of per-step dicts {'rewards': ..., "
            "'agent_raises': ...} in the same order the steps are declared in "
            "task.toml."
        )

    task_dir = Path(task_dir)
    trials_dir = Path(tempfile.mkdtemp())

    from harbor.environments.base import ExecResult
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial

    trial_kwargs: dict[str, Any] = {
        "task": {"path": str(task_dir)},
        "trials_dir": trials_dir,
    }
    if verifier_disabled:
        trial_kwargs["verifier"] = {"disable": True}
    config = TrialConfig(**trial_kwargs)
    trial_dir = trials_dir / config.trial_name

    env = _make_environment(is_mounted=is_mounted)
    agent = _make_agent()

    agent_instructions: list[str | None] = []
    agent_users: list[Any] = []
    artifact_downloads: list[list[Any]] = []
    verify_calls = {"n": 0}

    def _rt(idx: int) -> dict[str, Any]:
        return runtime[idx] if idx < len(runtime) else {}

    async def mock_exec(command: str, **kwargs: Any) -> Any:
        # The verifier running test.sh is the command carrying the 2>&1
        # redirect; that's our hook to write the step's reward file.
        if "2>&1" in command:
            idx = verify_calls["n"]
            verify_calls["n"] += 1
            _inject_reward(trial_dir / "verifier", _rt(idx).get("rewards"))
        return ExecResult(stdout="/app\n", stderr="", return_code=0)

    async def mock_agent_run(*args: Any, **kwargs: Any) -> None:
        instruction = kwargs.get("instruction")
        agent_instructions.append(instruction)
        agent_users.append(env.default_user)
        idx = len(agent_instructions) - 1
        if _rt(idx).get("agent_raises"):
            raise asyncio.TimeoutError("simulated fatal step")

    async def mock_download_file(source_path: str, target_path: Any) -> None:
        artifact_downloads.append([source_path, _step_name_from_target(target_path)])
        return None

    env.exec = AsyncMock(side_effect=mock_exec)
    agent.run = AsyncMock(side_effect=mock_agent_run)
    if not is_mounted:
        env.is_dir = AsyncMock(return_value=False)
        env.download_dir = AsyncMock(return_value=None)
        env.download_file = AsyncMock(side_effect=mock_download_file)

    async def _drive() -> Any:
        with (
            patch(ENV_FACTORY, return_value=env),
            patch(AGENT_FACTORY, return_value=agent),
        ):
            trial = await Trial.create(config=config)
            return await trial.run()

    result = asyncio.run(_drive())

    # Read the per-step result model through the discovered attribute names
    # (result_shape) — never hardcoded — so any valid result-model shape works.
    rs = result_shape
    step_results = getattr(result, rs["step_results_attr"]) or []

    def name_of(sr: Any) -> Any:
        return getattr(sr, rs["step_name_attr"])

    def rewards_of(sr: Any) -> dict[str, float] | None:
        return _path_get(sr, rs["step_rewards_path"])

    def raised(sr: Any) -> bool:
        return getattr(sr, rs["step_exception_attr"]) is not None

    return {
        "step_names": [name_of(sr) for sr in step_results],
        "n_executed": len(step_results),
        "step_rewards": [rewards_of(sr) for sr in step_results],
        "trial_rewards": (
            result.verifier_result.rewards
            if result.verifier_result is not None
            else None
        ),
        "exceptions": [raised(sr) for sr in step_results],
        "agent_instructions": agent_instructions,
        "agent_users": agent_users,
        "artifact_downloads": artifact_downloads,
        "trial_dir": str(trial_dir),
    }
