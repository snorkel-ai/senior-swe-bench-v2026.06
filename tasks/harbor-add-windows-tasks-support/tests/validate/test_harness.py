"""Test harness for harbor-add-windows-tasks-support validation stories.

Framework plumbing only: scaffolding on-disk task directories, building
task-config TOML, and constructing a minimal ``Trial`` so its OS-compat
preflight can be exercised in isolation. It asserts no outcome; the stories (in
``validation_spec.toml``) import the public surfaces under test and assert.

Loads only the harbor package's public surface
(``harbor.models.task.config``, ``harbor.models.task.paths``,
``harbor.models.trial.paths``, ``harbor.trial.trial``) via an ordinary editable
install (see ``validation-setup.sh``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Task-directory scaffolding (mirrors the repo's own test fixtures)
# ---------------------------------------------------------------------------


def scaffold_task_dir(
    base_dir: Path | str,
    *,
    test_ext: str = ".sh",
    solve_ext: str = ".sh",
) -> Path:
    """Create a minimal, otherwise-valid task directory under ``base_dir``.

    Lays down ``instruction.md``, ``task.toml``, ``environment/Dockerfile``,
    a ``tests/test<test_ext>`` script and a ``solution/solve<solve_ext>``
    script. Returns the task directory path (``base_dir`` itself).
    """
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    (base / "instruction.md").write_text("Do something")
    (base / "task.toml").write_text("[verifier]\ntimeout_sec = 60.0\n")
    (base / "environment").mkdir(exist_ok=True)
    (base / "environment" / "Dockerfile").write_text("FROM alpine")
    (base / "tests").mkdir(exist_ok=True)
    (base / f"tests/test{test_ext}").write_text("#!/bin/bash\necho ok")
    (base / "solution").mkdir(exist_ok=True)
    (base / f"solution/solve{solve_ext}").write_text("#!/bin/bash\necho solved")
    return base


def make_script_dir(base_dir: Path | str, *filenames: str) -> Path:
    """Create ``base_dir`` containing exactly the named (empty) files.

    Used for exercising standalone script discovery over a plain directory
    (e.g. a dir holding ``test.sh`` and/or ``test.bat``).
    """
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (base / name).write_text("#!/bin/bash\necho ok")
    return base


def build_task_config_toml(
    *,
    version: str | None = None,
    os_field: str | None = None,
    os_value: str | None = None,
) -> str:
    """Build a minimal task-config TOML string.

    ``version`` is written as the legacy top-level ``version`` key (the
    framework renames it to its schema-version field on load). When BOTH
    ``os_field`` and ``os_value`` are given, the line ``<os_field> =
    "<os_value>"`` is written under ``[environment]``; pass neither to omit
    the OS declaration entirely (modelling a pre-existing Linux task). The
    caller supplies ``os_field`` — this helper does not assume what the OS
    field on the environment config is called. All other config fields fall
    back to framework defaults.
    """
    lines: list[str] = []
    if version is not None:
        lines.append(f'version = "{version}"')
    lines.append("")
    lines.append("[environment]")
    if os_field is not None and os_value is not None:
        lines.append(f'{os_field} = "{os_value}"')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Trial preflight harness
# ---------------------------------------------------------------------------


def make_preflight_trial(*, tmp_path: Path | str, task_os_member: Any, agent: Any):
    """Assemble a minimal ``Trial`` around a caller-built ``agent``.

    Returns ``(trial, agent)`` where ``trial`` is built via ``Trial.__new__``
    with the supplied ``agent`` and a MagicMock environment + task, ready for
    ``await trial._setup_agent()`` (or for a separate, pre-setup guard method
    the implementation may define).

    The requested OS (``task_os_member``, an enum member discovered and built
    by the caller) is exposed on EVERY surface a preflight might read it from,
    under several plausible attribute names, so the harness does not assume
    where the implementation looks for the task OS:
      * on the environment: ``task_os`` / ``operating_system`` / ``os``, and
        the same names under its ``task_env_config`` / ``task_config.environment``
        accessors;
      * on the trial's task: ``os`` / ``operating_system`` / ``target_os`` and
        the same names under ``task.config.environment``.

    This helper deliberately does NOT build the agent or decide how the agent
    advertises Windows support — the caller constructs ``agent`` (e.g. a
    MagicMock) and sets the capability via whatever mechanism the
    implementation uses (a class attribute or a method). Inspect
    ``agent.setup.await_count`` to see whether the (mocked, async) agent setup
    was reached.
    """
    from unittest.mock import AsyncMock, MagicMock

    from harbor.models.trial.paths import TrialPaths
    from harbor.trial.trial import Trial

    environment = MagicMock()
    for name in ("task_os", "operating_system", "os"):
        setattr(environment, name, task_os_member)
    # Some implementations read the OS off the environment's task-config
    # accessor rather than a bare attribute; expose it there too so the guard
    # is not silently bypassed (a bare MagicMock would otherwise return a
    # truthy mock that never equals the windows enum member).
    for name in ("os", "operating_system", "target_os"):
        setattr(environment.task_env_config, name, task_os_member)
        setattr(environment.task_config.environment, name, task_os_member)

    task = MagicMock()
    task.name = "preflight-task"
    for name in ("os", "operating_system", "target_os"):
        setattr(task, name, task_os_member)
        setattr(task.config.environment, name, task_os_member)

    trial_dir = Path(tmp_path) / "trial"
    trial_dir.mkdir(parents=True, exist_ok=True)
    trial_paths = TrialPaths(trial_dir=trial_dir)
    trial_paths.mkdir()

    trial = Trial.__new__(Trial)
    trial._agent = agent
    trial._environment = environment
    trial._task = task
    trial._agent_setup_timeout_sec = 60
    trial._result = MagicMock()
    trial._invoke_hooks = AsyncMock()
    return trial, agent


def script_name_or_none(path: Any) -> str:
    """Return ``path.name`` for a discovered script, or the sentinel
    ``"__none__"`` when discovery returned ``None``.

    TOML cannot encode null, so stories use ``"__none__"`` as the expected
    value when a discovery is supposed to find nothing.
    """
    return "__none__" if path is None else Path(path).name
