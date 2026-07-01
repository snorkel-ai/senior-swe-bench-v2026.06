"""Test harness for prefect-fix-subflow-cancellation validation stories.

Everything in this module goes through pre-existing public Prefect
interfaces only, and stays implementation-agnostic: it observes
user-visible end-to-end behaviour without prescribing how a solution
delivers cancel intent across the runner/child boundary.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


PARENT_AND_CHILD_MODULE = '''\
"""Synthetic parent/child flows for the integration-test pattern.

Used by the validation harness — pre-existing Prefect public interfaces
only.
"""
import asyncio
import time
from pathlib import Path

from prefect import flow


def parent_cancel_hook(flow, flow_run, state):
    Path(flow_run.parameters["marker_dir"], "parent-cancelled").write_text(
        str(flow_run.id)
    )


def child_cancel_hook(flow, flow_run, state):
    Path(flow_run.parameters["marker_dir"], "child-cancelled").write_text(
        str(flow_run.id)
    )


# ── Busy parent + busy child for cancellation tests ──────────────────────
# The child writes a "child-started" marker, then loops on a sleep so the
# story can wait for it to be RUNNING, fire a cancel, and observe the
# on_cancellation hook side-effect.

@flow(on_cancellation=[child_cancel_hook], log_prints=True)
def busy_child_sync(marker_dir: str):
    Path(marker_dir, "child-started").write_text("started")
    while True:
        time.sleep(0.2)


@flow(on_cancellation=[parent_cancel_hook], log_prints=True)
def busy_parent_sync(marker_dir: str):
    busy_child_sync(marker_dir)


@flow(on_cancellation=[child_cancel_hook], log_prints=True)
async def busy_child_async(marker_dir: str):
    Path(marker_dir, "child-started").write_text("started")
    while True:
        await asyncio.sleep(0.2)


@flow(on_cancellation=[parent_cancel_hook], log_prints=True)
async def busy_parent_async(marker_dir: str):
    await busy_child_async(marker_dir)


# ── Quick parent + quick child for the no-cancellation regression test ──
# The child writes a "child-started" marker, returns 42; parent calls it,
# writes a "parent-finished" marker, returns the child's result.

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


REPO_DIR = Path("/repo/prefect")


def _uv_bin() -> str:
    found = shutil.which("uv")
    if found:
        return found
    fallback = "/usr/local/bin/uv"
    if Path(fallback).exists():
        return fallback
    raise RuntimeError("uv binary not found")


def _ensure_api_url() -> str:
    api_url = os.environ.get("PREFECT_API_URL")
    if not api_url:
        raise RuntimeError(
            "PREFECT_API_URL env var is not set. tests/test-setup.sh and "
            "validate/validation-setup.sh both export it pointing at the "
            "ephemeral Prefect server. If you reach this from a fresh "
            "subprocess, propagate the variable explicitly via env=."
        )
    return api_url


def _scratch_dir(tag: str) -> Path:
    """Make and return a fresh per-test scratch dir under /tmp."""
    root = Path("/tmp") / f"vh-{tag}-{uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "flows").mkdir(exist_ok=True)
    (root / "markers").mkdir(exist_ok=True)
    return root


def _write_flow_module(flow_dir: Path) -> Path:
    flow_path = flow_dir / "parent_and_child.py"
    flow_path.write_text(PARENT_AND_CHILD_MODULE)
    return flow_path


def _create_work_pool(name: str, env: dict[str, str]) -> None:
    subprocess.check_call(
        [
            _uv_bin(),
            "run",
            "prefect",
            "work-pool",
            "create",
            name,
            "-t",
            "process",
        ],
        cwd=str(REPO_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        timeout=120,
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
        pass  # Best-effort cleanup — never let cleanup failure mask a test result.


def _execute_flow_run(
    flow_run_id: UUID,
    flow_dir: Path,
    log_path: Path,
    env: dict[str, str],
) -> tuple[subprocess.Popen, Any]:
    log_file = log_path.open("w")
    proc = subprocess.Popen(
        [
            _uv_bin(),
            "run",
            "prefect",
            "flow-run",
            "execute",
            str(flow_run_id),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(REPO_DIR),
        env=env,
    )
    return proc, log_file


def _read_state_name(flow_run) -> str:
    """Return the friendly state name (e.g. "Cancelled", "Crashed",
    "Completed") or an empty string when no state is known yet.
    """
    if flow_run is None or flow_run.state is None:
        return ""
    return flow_run.state.name or ""


def _state_type_name(flow_run) -> str:
    """Return the state-type name (e.g. "CANCELLED", "CRASHED")."""
    if flow_run is None or flow_run.state is None:
        return ""
    return getattr(flow_run.state.type, "name", "") or str(flow_run.state.type)


def run_nested_cancellation_test(
    *,
    async_flows: bool,
    parent_running_timeout: float = 90.0,
    child_running_timeout: float = 90.0,
    cancel_propagation_timeout: float = 120.0,
) -> dict:
    """Run the deploy → execute → cancel → observe pattern.

    Sets up a process work pool, deploys a busy parent flow that calls a
    busy child flow in-process, runs ``prefect flow-run execute`` as a
    subprocess, waits for both to be RUNNING, sets the parent state to
    Cancelling via the API, waits for the subprocess to exit, then reads
    terminal states and on_cancellation marker file evidence.

    Parameters
    ----------
    async_flows
        If True, use the async parent + async child entrypoint.

    Returns a dict with concrete observable values:
        parent_terminal_state           — e.g. "Cancelled" / "Crashed" / "Running" / ""
        child_terminal_state            — same
        parent_terminal_state_type      — e.g. "CANCELLED" / "CRASHED" (state.type.name)
        child_terminal_state_type       — same
        parent_hook_marker_present      — bool: parent on_cancellation hook fired
        child_hook_marker_present       — bool: child on_cancellation hook fired
        parent_hook_marker_content      — str: contents of parent-cancelled file (the parent flow_run_id, or "")
        child_hook_marker_content       — str: contents of child-cancelled file (the child flow_run_id, or "")
        parent_hook_marker_matches_id   — bool: parent marker contents == parent flow_run_id
        child_hook_marker_matches_id    — bool: child marker contents == child flow_run_id
        subprocess_exit_code            — int: rc from `prefect flow-run execute`
        child_run_observed              — bool: did we ever observe a child flow run via the API?
        log_tail                        — str: last few KB of subprocess output (for diagnostics)
    """
    api_url = _ensure_api_url()
    env = {**os.environ, "PREFECT_API_URL": api_url}

    # Lazy imports — keep module-level imports of prefect minimal so the
    # harness can be imported in environments where Prefect isn't fully
    # installed yet (during validation-setup smoke checks, for instance).
    import prefect
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterParentFlowRunId,
    )
    from prefect.client.schemas.sorting import FlowRunSort
    from prefect.states import Cancelling

    scratch = _scratch_dir("cancel")
    flow_dir = scratch / "flows"
    marker_dir = scratch / "markers"
    log_path = scratch / "execute.log"

    _write_flow_module(flow_dir)

    work_pool = f"vh-cancel-pool-{uuid4().hex[:8]}"
    deployment_name = f"vh-cancel-dep-{uuid4().hex[:8]}"
    entrypoint = (
        "parent_and_child.py:busy_parent_async"
        if async_flows
        else "parent_and_child.py:busy_parent_sync"
    )

    work_pool_created = False
    deployment_id: UUID | None = None
    proc: subprocess.Popen | None = None
    log_file = None
    parent_run_id: UUID | None = None
    child_run_id: UUID | None = None
    rc: int | None = None
    child_run_observed = False

    result: dict[str, Any] = {
        "parent_terminal_state": "",
        "child_terminal_state": "",
        "parent_terminal_state_type": "",
        "child_terminal_state_type": "",
        "parent_hook_marker_present": False,
        "child_hook_marker_present": False,
        "parent_hook_marker_content": "",
        "child_hook_marker_content": "",
        "parent_hook_marker_matches_id": False,
        "child_hook_marker_matches_id": False,
        "subprocess_exit_code": -1,
        "child_run_observed": False,
        "log_tail": "",
    }

    try:
        _create_work_pool(work_pool, env)
        work_pool_created = True

        deployment_id = prefect.flow.from_source(
            source=str(flow_dir),
            entrypoint=entrypoint,
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

        proc, log_file = _execute_flow_run(parent_run_id, flow_dir, log_path, env)

        # Wait for parent to be RUNNING.
        deadline = time.monotonic() + parent_running_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break  # subprocess already exited; bail and read final states
            with get_client(sync_client=True) as client:
                run = client.read_flow_run(parent_run_id)
            if run.state and run.state.is_running():
                break
            time.sleep(0.5)

        # Wait for the child to be RUNNING. Use the started marker as a
        # cheap pre-check, then confirm via the API.
        deadline = time.monotonic() + child_running_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if (marker_dir / "child-started").exists():
                with get_client(sync_client=True) as client:
                    children = client.read_flow_runs(
                        flow_run_filter=FlowRunFilter(
                            parent_flow_run_id=FlowRunFilterParentFlowRunId(
                                any_=[parent_run_id]
                            )
                        ),
                        sort=FlowRunSort.EXPECTED_START_TIME_ASC,
                    )
                running = [c for c in children if c.state and c.state.is_running()]
                if running:
                    child_run_id = running[0].id
                    child_run_observed = True
                    break
            time.sleep(0.5)

        # Issue cancellation via the public API.
        with get_client(sync_client=True) as client:
            client.set_flow_run_state(parent_run_id, Cancelling())

        # Wait for the subprocess to exit.
        try:
            rc = proc.wait(timeout=cancel_propagation_timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.wait(timeout=10)

        # Read terminal states.
        with get_client(sync_client=True) as client:
            parent_final = client.read_flow_run(parent_run_id)
            children_final = client.read_flow_runs(
                flow_run_filter=FlowRunFilter(
                    parent_flow_run_id=FlowRunFilterParentFlowRunId(
                        any_=[parent_run_id]
                    )
                ),
                sort=FlowRunSort.EXPECTED_START_TIME_ASC,
            )

        if not child_run_observed and children_final:
            # Child was created but maybe didn't reach RUNNING in time.
            child_run_id = children_final[0].id
            child_run_observed = True

        child_final = next(
            (c for c in children_final if child_run_id and c.id == child_run_id), None
        )

        result["parent_terminal_state"] = _read_state_name(parent_final)
        result["child_terminal_state"] = _read_state_name(child_final)
        result["parent_terminal_state_type"] = _state_type_name(parent_final)
        result["child_terminal_state_type"] = _state_type_name(child_final)
        result["subprocess_exit_code"] = rc if rc is not None else -1
        result["child_run_observed"] = child_run_observed

        parent_marker = marker_dir / "parent-cancelled"
        child_marker = marker_dir / "child-cancelled"
        result["parent_hook_marker_present"] = parent_marker.exists()
        result["child_hook_marker_present"] = child_marker.exists()
        result["parent_hook_marker_content"] = (
            parent_marker.read_text() if parent_marker.exists() else ""
        )
        result["child_hook_marker_content"] = (
            child_marker.read_text() if child_marker.exists() else ""
        )
        result["parent_hook_marker_matches_id"] = (
            result["parent_hook_marker_content"] == str(parent_run_id)
            if parent_run_id
            else False
        )
        result["child_hook_marker_matches_id"] = (
            result["child_hook_marker_content"] == str(child_run_id)
            if child_run_id
            else False
        )

        if log_path.exists():
            result["log_tail"] = log_path.read_text()[-3000:]

    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=15)
                except Exception:
                    pass
            except Exception:
                pass
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        if deployment_id is not None:
            try:
                with get_client(sync_client=True) as client:
                    client.delete_deployment(deployment_id)
            except Exception:
                pass
        if work_pool_created:
            _delete_work_pool(work_pool, env)

    return result


def run_nested_completion_test(
    *,
    completion_timeout: float = 180.0,
) -> dict:
    """Deploy + run a normal (non-cancelled) parent calling a child in
    process. Both must reach COMPLETED.

    Returns a dict with concrete observable values:
        parent_terminal_state           — "Completed" on success
        child_terminal_state            — "Completed" on success
        parent_terminal_state_type      — "COMPLETED" on success
        child_terminal_state_type       — same
        child_started_marker_present    — bool: child body executed
        child_finished_marker_present   — bool: child body returned
        parent_finished_marker_present  — bool: parent body returned
        subprocess_exit_code            — int (0 on success)
        log_tail                        — str: last few KB of subprocess output
    """
    api_url = _ensure_api_url()
    env = {**os.environ, "PREFECT_API_URL": api_url}

    import prefect
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.filters import (
        FlowRunFilter,
        FlowRunFilterParentFlowRunId,
    )
    from prefect.client.schemas.sorting import FlowRunSort

    scratch = _scratch_dir("done")
    flow_dir = scratch / "flows"
    marker_dir = scratch / "markers"
    log_path = scratch / "execute.log"

    _write_flow_module(flow_dir)

    work_pool = f"vh-done-pool-{uuid4().hex[:8]}"
    deployment_name = f"vh-done-dep-{uuid4().hex[:8]}"
    entrypoint = "parent_and_child.py:quick_parent_sync"

    work_pool_created = False
    deployment_id: UUID | None = None
    proc: subprocess.Popen | None = None
    log_file = None
    parent_run_id: UUID | None = None
    rc: int | None = None

    result: dict[str, Any] = {
        "parent_terminal_state": "",
        "child_terminal_state": "",
        "parent_terminal_state_type": "",
        "child_terminal_state_type": "",
        "child_started_marker_present": False,
        "child_finished_marker_present": False,
        "parent_finished_marker_present": False,
        "subprocess_exit_code": -1,
        "log_tail": "",
    }

    try:
        _create_work_pool(work_pool, env)
        work_pool_created = True

        deployment_id = prefect.flow.from_source(
            source=str(flow_dir),
            entrypoint=entrypoint,
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

        proc, log_file = _execute_flow_run(parent_run_id, flow_dir, log_path, env)

        try:
            rc = proc.wait(timeout=completion_timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.wait(timeout=10)

        with get_client(sync_client=True) as client:
            parent_final = client.read_flow_run(parent_run_id)
            children_final = client.read_flow_runs(
                flow_run_filter=FlowRunFilter(
                    parent_flow_run_id=FlowRunFilterParentFlowRunId(
                        any_=[parent_run_id]
                    )
                ),
                sort=FlowRunSort.EXPECTED_START_TIME_ASC,
            )
        child_final = children_final[0] if children_final else None

        result["parent_terminal_state"] = _read_state_name(parent_final)
        result["child_terminal_state"] = _read_state_name(child_final)
        result["parent_terminal_state_type"] = _state_type_name(parent_final)
        result["child_terminal_state_type"] = _state_type_name(child_final)
        result["subprocess_exit_code"] = rc if rc is not None else -1
        result["child_started_marker_present"] = (marker_dir / "child-started").exists()
        result["child_finished_marker_present"] = (
            marker_dir / "child-finished"
        ).exists()
        result["parent_finished_marker_present"] = (
            marker_dir / "parent-finished"
        ).exists()
        if log_path.exists():
            result["log_tail"] = log_path.read_text()[-3000:]

    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=15)
                except Exception:
                    pass
            except Exception:
                pass
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        if deployment_id is not None:
            try:
                with get_client(sync_client=True) as client:
                    client.delete_deployment(deployment_id)
            except Exception:
                pass
        if work_pool_created:
            _delete_work_pool(work_pool, env)

    return result
