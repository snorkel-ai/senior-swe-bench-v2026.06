"""Single-step regression guard for the multi-step trials feature.

Exercised through the pre-existing public ``Trial.run`` surface, asserting only
pre-existing observables (the agent runs once and the trial-root artifacts
manifest is written). References no feature-introduced attribute, so it passes on
both the unfixed and fixed trees and pins nothing the agent designs.

Runs via ``/repo/harbor/.venv/bin/python -m pytest`` (see verify.toml) with
cwd=/repo/harbor, so ``import harbor...`` resolves to the editable install.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

ENV_FACTORY = "harbor.trial.trial.EnvironmentFactory.create_environment_from_config"
AGENT_FACTORY = "harbor.trial.trial.AgentFactory.create_agent_from_config"

DOCKERFILE = "FROM ubuntu:24.04\nWORKDIR /app\n"
STEP_TEST_SH = "#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n"


def _write_single_step_task(task_dir: Path) -> Path:
    """Write a classic single-step task directory on disk."""
    env_dir = task_dir / "environment"
    tests_dir = task_dir / "tests"
    env_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / "Dockerfile").write_text(DOCKERFILE)
    (task_dir / "task.toml").write_text(
        "[environment]\nbuild_timeout_sec = 60.0\n\n"
        "[agent]\ntimeout_sec = 10.0\n\n"
        "[verifier]\ntimeout_sec = 10.0\n"
    )
    (task_dir / "instruction.md").write_text("Do something.\n")
    (tests_dir / "test.sh").write_text(STEP_TEST_SH)
    return task_dir


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


def _make_environment() -> AsyncMock:
    from harbor.environments.base import ExecResult

    env = AsyncMock()
    env.is_mounted = False
    env.exec.return_value = ExecResult(stdout="/app\n", stderr="", return_code=0)
    env.upload_dir.return_value = None
    env.upload_file.return_value = None
    env.start.return_value = None
    env.stop.return_value = None
    env.download_dir = AsyncMock(return_value=None)
    return env


def test_single_step_trial_unchanged(tmp_path: Path) -> None:
    """A task with no steps keeps its exact current behavior: the agent runs
    exactly once and the trial-root artifacts manifest is written."""
    work = tmp_path
    task_dir = work / "task"
    trials_dir = work / "trials"
    _write_single_step_task(task_dir)

    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial

    config = TrialConfig(
        task={"path": str(task_dir)},
        trials_dir=trials_dir,
        verifier={"disable": True},
    )
    trial_dir = trials_dir / config.trial_name

    env = _make_environment()
    agent = _make_agent()

    async def _drive() -> Any:
        with (
            patch(ENV_FACTORY, return_value=env),
            patch(AGENT_FACTORY, return_value=agent),
        ):
            trial = await Trial.create(config=config)
            return await trial.run()

    asyncio.run(_drive())

    # Regression guard through the PRE-EXISTING Trial.run surface only: the
    # single-step path still runs the agent exactly once and writes the
    # trial-root artifacts manifest, exactly as before the feature. We assert
    # nothing about the new per-step result collection — whether a single-step
    # trial leaves it empty or an implementation reuses the step machinery
    # internally is an implementation detail, not the pre-existing contract.
    assert agent.run.call_count == 1
    assert (trial_dir / "artifacts" / "manifest.json").is_file()
