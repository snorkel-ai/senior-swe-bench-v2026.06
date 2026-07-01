"""Behavioral verifier for prefect-fix-subflow-cancellation.

Tested through pre-existing public interfaces only (CLI subcommand,
deployment API, PrefectClient API, ``@flow`` decorator). To keep the
verifier robust against any chosen implementation, it deliberately avoids
referencing internal control-mechanism modules, env vars, or helpers a
solution might add.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest

# Per-test scratch root. Avoids ``tmp_path`` (which pytest tries to
# clean up while the subprocess might still be writing to it on slow
# teardown).
SCRATCH_ROOT = Path("/tmp") / f"verify-prefect-fix-subflow-cancellation-{uuid4().hex[:8]}"

PARENT_AND_CHILD_MODULE = textwrap.dedent(
    '''\
    """Synthetic parent + child flows for the no-cancellation regression check.

    Pre-existing Prefect public interfaces only — ``@flow`` decorator,
    nothing implementation-specific.
    """
    from pathlib import Path
    from prefect import flow

    @flow(log_prints=True)
    def quick_child_sync(marker_dir: str) -> int:
        Path(marker_dir, "child-started").write_text("started")
        Path(marker_dir, "child-finished").write_text("finished")
        return 42

    @flow(log_prints=True)
    def quick_parent_sync(marker_dir: str) -> int:
        result = quick_child_sync(marker_dir)
        Path(marker_dir, "parent-finished").write_text("finished")
        return result
    '''
)


def _uv_bin() -> str:
    """Resolve the uv binary path (uv is installed in /usr/local/bin)."""
    import shutil

    found = shutil.which("uv")
    if found:
        return found
    fallback = "/usr/local/bin/uv"
    if Path(fallback).exists():
        return fallback
    raise RuntimeError("uv binary not found")


REPO_DIR = Path("/repo/prefect")


def _create_work_pool(name: str, env: dict[str, str]) -> None:
    subprocess.check_call(
        [_uv_bin(), "run", "prefect", "work-pool", "create", name, "-t", "process"],
        cwd=str(REPO_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def _delete_work_pool(name: str, env: dict[str, str]) -> None:
    try:
        subprocess.check_call(
            [
                _uv_bin(),
                "run",
                "prefect",
                "--no-prompt",
                "work-pool",
                "delete",
                name,
            ],
            cwd=str(REPO_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            timeout=60,
        )
    except Exception:
        pass


@pytest.mark.timeout(300)
def test_non_cancelled_nested_flow_completes() -> None:
    """A normal parent calling a child in-process completes — both reach
    COMPLETED.
    """
    api_url = os.environ.get("PREFECT_API_URL")
    assert api_url, "PREFECT_API_URL must be set by test-setup.sh"

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    flow_dir = SCRATCH_ROOT / "flows"
    flow_dir.mkdir(exist_ok=True)
    marker_dir = SCRATCH_ROOT / "markers"
    marker_dir.mkdir(exist_ok=True)

    flow_module_path = flow_dir / "parent_and_child.py"
    flow_module_path.write_text(PARENT_AND_CHILD_MODULE)

    work_pool = f"verify-pool-{uuid4().hex[:8]}"
    deployment_name = f"verify-dep-{uuid4().hex[:8]}"

    env = {**os.environ, "PREFECT_API_URL": api_url}

    # Lazy import — only needed inside the test, not at collection time.
    import prefect
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterParentFlowRunId,
    )
    from prefect.client.schemas.sorting import FlowRunSort

    work_pool_created = False
    deployment_id: UUID | None = None
    proc: subprocess.Popen | None = None
    log_path = SCRATCH_ROOT / "execute.log"
    log_file = None

    try:
        _create_work_pool(work_pool, env)
        work_pool_created = True

        deployment_id = prefect.flow.from_source(
            source=str(flow_dir),
            entrypoint="parent_and_child.py:quick_parent_sync",
        ).deploy(
            name=deployment_name,
            work_pool_name=work_pool,
            parameters={"marker_dir": str(marker_dir)},
            build=False,
            push=False,
            print_next_steps=False,
            ignore_warnings=True,
        )

        with get_client(sync_client=True) as client:
            parent_run = client.create_flow_run_from_deployment(deployment_id)
        parent_run_id = parent_run.id

        log_file = log_path.open("w")
        proc = subprocess.Popen(
            [
                _uv_bin(),
                "run",
                "prefect",
                "flow-run",
                "execute",
                str(parent_run_id),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(REPO_DIR),
            env=env,
        )

        # The flow should run to completion well within 90s.
        rc = proc.wait(timeout=180)

        # Read terminal states.
        with get_client(sync_client=True) as client:
            parent_final = client.read_flow_run(parent_run_id)
            children = client.read_flow_runs(
                flow_run_filter=FlowRunFilter(
                    parent_flow_run_id=FlowRunFilterParentFlowRunId(
                        any_=[parent_run_id]
                    )
                ),
                sort=FlowRunSort.EXPECTED_START_TIME_ASC,
            )

        log_tail = log_path.read_text()[-3000:] if log_path.exists() else "<no log>"

        assert rc == 0, (
            f"`prefect flow-run execute` exited non-zero ({rc}). "
            f"Last 3 KB of subprocess log:\n{log_tail}"
        )
        assert parent_final.state and parent_final.state.is_completed(), (
            f"Parent flow run did not reach COMPLETED. "
            f"State: {parent_final.state.type if parent_final.state else None} "
            f"name={parent_final.state.name if parent_final.state else None}.\n"
            f"Log tail:\n{log_tail}"
        )
        assert len(children) == 1, (
            f"Expected exactly one child subflow run; got {len(children)}.\n"
            f"Log tail:\n{log_tail}"
        )
        child = children[0]
        assert child.state and child.state.is_completed(), (
            f"Child subflow run did not reach COMPLETED. "
            f"State: {child.state.type if child.state else None} "
            f"name={child.state.name if child.state else None}.\n"
            f"Log tail:\n{log_tail}"
        )

        # The user-side hooks-and-side-effects from the flow body should
        # have run, too — child wrote its finished marker, parent wrote its
        # finished marker.
        assert (marker_dir / "child-finished").exists(), (
            "Child finished marker missing — child body did not run to completion.\n"
            f"Log tail:\n{log_tail}"
        )
        assert (marker_dir / "parent-finished").exists(), (
            "Parent finished marker missing — parent body did not run to completion.\n"
            f"Log tail:\n{log_tail}"
        )

    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=15)
        if log_file is not None:
            log_file.close()
        if deployment_id is not None:
            try:
                with get_client(sync_client=True) as client:
                    client.delete_deployment(deployment_id)
            except Exception:
                pass
        if work_pool_created:
            _delete_work_pool(work_pool, env)
