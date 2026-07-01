"""Test harness for prefect-feat-dbt-per-node-hardening validation stories.

Wraps the prefect-dbt repo's pre-existing test infrastructure (the
conftest.py helpers under
`/repo/prefect/src/integrations/prefect-dbt/tests/core/`) for manifest
synthesis, mock-executor wiring, and Prefect client plumbing.

Helpers reach into `PrefectDbtOrchestrator`, `ExecutionMode`, `TestStrategy`,
and `CacheConfig` — all pre-existing public surface on
`prefect_dbt.core._orchestrator` at the base commit. The failure opt-out
parameter NAME is discovered at runtime via `inspect.signature` substring
matching (see `discover_failure_optout`) so the harness stays agnostic to
whatever the implementation calls it.

Usage from a validation script::

    import sys
    sys.path.insert(0, "/tests/validate")
    import test_harness as th
"""

from __future__ import annotations

import asyncio
import inspect
import shutil
from pathlib import Path
from typing import Any, Mapping

import importlib.util as _importlib_util

# Load tests/core/conftest.py explicitly by path (it relies on pytest's plugin
# loader, not normal imports) so its name never collides with the
# pytest-generated conftest the validation driver drops alongside story scripts.
_TESTS_CORE = Path("/repo/prefect/src/integrations/prefect-dbt/tests/core")
_TESTS_CORE_CONFTEST_PATH = _TESTS_CORE / "conftest.py"
_spec = _importlib_util.spec_from_file_location(
    "_dbt_tests_core_conftest", _TESTS_CORE_CONFTEST_PATH
)
_dbt_conftest = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_dbt_conftest)  # type: ignore[union-attr]

# Re-export the helpers under stable names.
_make_mock_executor_per_node = _dbt_conftest._make_mock_executor_per_node
_make_mock_settings = _dbt_conftest._make_mock_settings
_make_node = _dbt_conftest._make_node
_write_manifest_in_dir = _dbt_conftest.write_manifest
_write_sql_files = _dbt_conftest.write_sql_files

# The bundled real-dbt DuckDB project (pre-existing test fixture in the repo).
DBT_TEST_PROJECT = (
    Path("/repo/prefect/src/integrations/prefect-dbt/tests/dbt_test_project")
)


def make_mock_executor_per_node(
    success: bool = True,
    fail_nodes: set[str] | None = None,
    artifacts: dict[str, Any] | None = None,
):
    """Return a `MagicMock` satisfying the DbtExecutor protocol with a counted
    ``execute_node``."""
    return _make_mock_executor_per_node(
        success=success, artifacts=artifacts, fail_nodes=fail_nodes
    )


def make_mock_settings(project_dir: Path):
    """Return a mock `PrefectDbtSettings` rooted at `project_dir`."""
    return _make_mock_settings(project_dir=project_dir)


def write_manifest(project_dir: Path, data: Mapping[str, Any]) -> Path:
    """Write `data` as JSON to `<project_dir>/manifest.json`; return the path."""
    return _write_manifest_in_dir(project_dir, dict(data))


def write_sql_files(project_dir: Path, file_map: Mapping[str, str]) -> None:
    """Materialise SQL/CSV/macro files relative to `project_dir`."""
    _write_sql_files(project_dir, dict(file_map))


def build_per_node_orchestrator(
    *,
    project_dir: Path,
    manifest_path: Path,
    executor,
    cache: Any | None = None,
    test_strategy: Any | None = None,
    **extra,
):
    """Construct a PER_NODE `PrefectDbtOrchestrator` with a ThreadPoolTaskRunner.

    `cache` / `test_strategy` are pre-existing constructor inputs; pass them
    through only when a story needs them. `**extra` forwards any additional
    pre-existing constructor kwargs (e.g. the discovered failure opt-out
    parameter).
    """
    from prefect.task_runners import ThreadPoolTaskRunner
    from prefect_dbt.core._orchestrator import (
        ExecutionMode,
        PrefectDbtOrchestrator,
    )

    settings = make_mock_settings(project_dir=project_dir)
    kwargs: dict[str, Any] = {
        "settings": settings,
        "manifest_path": manifest_path,
        "executor": executor,
        "execution_mode": ExecutionMode.PER_NODE,
        "task_runner_type": ThreadPoolTaskRunner,
    }
    if cache is not None:
        kwargs["cache"] = cache
    if test_strategy is not None:
        kwargs["test_strategy"] = test_strategy
    kwargs.update(extra)
    return PrefectDbtOrchestrator(**kwargs)


def make_cache_config(result_storage: Path, key_storage: Path):
    """Return a pre-existing `CacheConfig` with persistent file storage.

    `result_storage` must be a Path (so Prefect creates a LocalFileSystem
    rather than trying `Block.load()` on a string); `key_storage` is passed
    as a string path.
    """
    from prefect_dbt.core._orchestrator import CacheConfig

    return CacheConfig(result_storage=Path(result_storage), key_storage=str(key_storage))


def immediate_test_strategy():
    """Return the pre-existing `TestStrategy.IMMEDIATE` enum value."""
    from prefect_dbt.core._orchestrator import TestStrategy

    return TestStrategy.IMMEDIATE


def build_real_dbt_orchestrator(tmp_path: Path, **kwargs):
    """Build a `PrefectDbtOrchestrator` over a real copy of the bundled DuckDB
    project (real `PrefectDbtSettings`, real dbtRunner, parsed manifest).

    Copies `tests/dbt_test_project` into `tmp_path`, writes a DuckDB
    profiles.yml, runs `dbt parse` to produce `target/manifest.json`, and
    returns an orchestrator defaulting to `TestStrategy.SKIP`.
    """
    import yaml
    from dbt.cli.main import dbtRunner

    from prefect_dbt.core._orchestrator import PrefectDbtOrchestrator, TestStrategy
    from prefect_dbt.core.settings import PrefectDbtSettings

    project_dir = Path(tmp_path) / "dbt_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    for item in DBT_TEST_PROJECT.iterdir():
        dest = project_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    profiles = {
        "test": {
            "target": "dev",
            "outputs": {
                "dev": {
                    "type": "duckdb",
                    "path": str(project_dir / "warehouse.duckdb"),
                    "schema": "main",
                    "threads": 1,
                }
            },
        }
    }
    (project_dir / "profiles.yml").write_text(yaml.dump(profiles))

    result = dbtRunner().invoke(
        [
            "parse",
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(project_dir),
        ]
    )
    assert result.success, f"dbt parse failed: {getattr(result, 'exception', None)}"
    manifest_path = project_dir / "target" / "manifest.json"
    assert manifest_path.exists(), "manifest.json not generated by dbt parse"

    kwargs.setdefault("test_strategy", TestStrategy.SKIP)
    settings = PrefectDbtSettings(project_dir=project_dir, profiles_dir=project_dir)
    return PrefectDbtOrchestrator(
        settings=settings, manifest_path=manifest_path, **kwargs
    )


# Flow execution helpers (PER_NODE needs an active flow run context).
def run_in_flow(orchestrator, **run_kwargs):
    """Call ``orchestrator.run_build(**run_kwargs)`` inside a Prefect @flow."""
    from prefect import flow

    @flow
    def _runner():
        return orchestrator.run_build(**run_kwargs)

    return _runner()


def run_twice_in_flow(orchestrator):
    """Run ``run_build()`` twice inside a single @flow; return ``(r1, r2)``."""
    from prefect import flow

    @flow
    def _runner():
        r1 = orchestrator.run_build()
        r2 = orchestrator.run_build()
        return r1, r2

    return _runner()


def run_build_capturing_flow_id(orchestrator, **run_kwargs):
    """Run ``run_build`` inside a @flow and capture the flow run id.

    Returns ``(result, flow_run_id)``. The id is read from the active run
    context — the canonical way to correlate the build with its persisted
    task runs.
    """
    from prefect import flow
    from prefect.context import get_run_context

    sink: dict[str, Any] = {}

    @flow
    def _runner():
        sink["flow_run_id"] = get_run_context().flow_run.id
        return orchestrator.run_build(**run_kwargs)

    result = _runner()
    return result, sink["flow_run_id"]


def fetch_task_runs(flow_run_id):
    """Read all task runs for a flow run via the Prefect client."""
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import FlowRunFilter, FlowRunFilterId

    async def _fetch():
        async with get_client() as client:
            return await client.read_task_runs(
                flow_run_filter=FlowRunFilter(id=FlowRunFilterId(any_=[flow_run_id]))
            )

    return asyncio.run(_fetch())


def wait_for_upstream_names(task_runs) -> dict[str, list[str]]:
    """Map each task run's name to the sorted names of its ``wait_for`` upstreams.

    Prefect persists declared non-data dependencies under
    ``task_inputs["wait_for"]`` as references carrying the upstream task-run
    id; this resolves those ids back to names so a story can assert the edge
    set with stable, human-readable values.
    """
    by_id = {tr.id: tr.name for tr in task_runs}
    out: dict[str, list[str]] = {}
    for tr in task_runs:
        inputs = tr.task_inputs or {}
        deps = inputs.get("wait_for", []) or []
        names = sorted(
            by_id[inp.id] for inp in deps if getattr(inp, "id", None) in by_id
        )
        out[tr.name] = names
    return out


def count_task_runs_with_wait_for(task_runs) -> int:
    """Count task runs that carry a non-empty ``wait_for`` dependency list."""
    n = 0
    for tr in task_runs:
        inputs = tr.task_inputs or {}
        if inputs.get("wait_for"):
            n += 1
    return n


# Failure opt-out discovery (the implementation chooses the parameter name).
def discover_failure_optout(orch_cls) -> tuple[str, bool] | None:
    """Find the boolean constructor parameter that toggles fail-on-error.

    The implementation owns the NAME (e.g. ``raise_on_failure``,
    ``fail_on_error``, ``strict``, ``suppress_failures``). Discovery is
    substring-based on the parameter name and requires a boolean default.

    The default value encodes the raising behaviour (the load-bearing
    default is "raise on error"), so the value that DISABLES raising is
    ``not default``. Returns ``(param_name, non_raising_value)`` or ``None``
    when no such parameter is exposed (the caller then skips the story).
    """
    sig = inspect.signature(orch_cls.__init__)
    markers = ("raise", "fail", "strict", "suppress", "error")
    for name, param in sig.parameters.items():
        if name in ("self", "args", "kwargs"):
            continue
        lower = name.lower()
        if not any(m in lower for m in markers):
            continue
        default = param.default
        if not isinstance(default, bool):
            continue
        return name, (not default)
    return None


import contextlib as _contextlib
import logging as _logging


@_contextlib.contextmanager
def capture_prefect_dbt_logs(level: int = _logging.WARNING):
    """Capture WARN/ERROR ``LogRecord``s emitted anywhere while the block runs.

    A handler is attached to the root logger so that warnings the integration
    re-surfaces through the standard logging module (e.g.
    ``logging.getLogger("prefect_dbt.core._manifest").warning(...)`` when an
    internal ``dbt ls`` reports a selector that matches no nodes) are captured
    regardless of which submodule emits them. Yields a list that fills with the
    captured records. An implementation that blanket-silences dbt output emits
    nothing here.
    """
    records: list = []

    class _Collector(_logging.Handler):
        def emit(self, record):  # noqa: D401 - simple collector
            records.append(record)

    handler = _Collector(level=level)
    root = _logging.getLogger()
    prev_level = root.level
    if root.level == _logging.NOTSET or root.level > level:
        root.setLevel(level)
    root.addHandler(handler)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)


def surfaced_messages(records, substring: str | None = None, level: int = _logging.WARNING):
    """Return the formatted messages of captured WARN/ERROR records.

    When ``substring`` is given, only records whose message contains it are
    returned (e.g. the selector token that matched no nodes), so a story can
    tie a surfaced warning to its cause without coupling to dbt's exact
    wording.
    """
    out: list[str] = []
    for r in records:
        if r.levelno < level:
            continue
        try:
            msg = r.getMessage()
        except Exception:
            msg = str(getattr(r, "msg", ""))
        if substring is None or substring in msg:
            out.append(msg)
    return out


def exception_haystack(exc: BaseException) -> str:
    """Build a broad searchable string from an exception's message and any
    string/collection attributes, so a story can assert that a failed node id
    is referenced without coupling to a specific message format or attribute
    name."""
    parts: list[str] = [str(exc), repr(exc)]
    for attr in dir(exc):
        if attr.startswith("__"):
            continue
        try:
            val = getattr(exc, attr)
        except Exception:
            continue
        if isinstance(val, (str, list, tuple, set, dict)):
            parts.append(str(val))
    return " ".join(parts)
