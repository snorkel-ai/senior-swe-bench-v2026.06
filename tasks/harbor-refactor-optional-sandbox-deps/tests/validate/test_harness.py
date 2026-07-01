"""Test harness for harbor-refactor-optional-sandbox-deps validation stories.

Framework plumbing ONLY. This module asserts no outcome, references no
Windows/cloud-vendor internal, and pins no solution-chosen public name. The
stories themselves (in ``validation_spec.toml``) import the pre-existing public
Harbor surfaces under test, discover the solution-chosen pieces from source +
``pyproject.toml``, and make the assertions.

ENVIRONMENT (set up by validation-setup.sh before the validation agent runs)
- Harbor repo:  /repo/harbor               (read freely; do NOT modify)
- Venv:         /repo/harbor/.venv         (python at .venv/bin/python)
- Available:    harbor (editable, --no-deps), pydantic, toml, shortuuid,
                tenacity, pytest, pytest-asyncio
- NOT present:  daytona, e2b, modal, runloop-api-client, kubernetes,
                dockerfile-parse — their absence is what the stories test

KEY INTERFACES (all pre-existing in harbor)
- ``harbor.environments.factory.EnvironmentFactory.create_environment``
- ``harbor.environments.base.BaseEnvironment``, ``ExecResult``
- ``harbor.models.environment_type.EnvironmentType``
- ``harbor.models.task.config.EnvironmentConfig``
- ``harbor.models.trial.paths.TrialPaths``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def make_trial_paths(tmp_path: Path | str) -> Any:
    """Build a ``TrialPaths`` rooted in a fresh directory under ``tmp_path``.

    Pure plumbing: every public Harbor surface that constructs an environment
    needs a ``TrialPaths`` built from a real, existing directory. The story
    decides what to do with the constructed environment and what to assert.
    """
    from harbor.models.trial.paths import TrialPaths

    trial_root = Path(tmp_path) / "trial"
    trial_root.mkdir(parents=True, exist_ok=True)
    return TrialPaths(trial_root)
