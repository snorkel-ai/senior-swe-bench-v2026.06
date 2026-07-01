"""Behavioral verifier for prefect-fix-task-run-recorder.

The ``task_run`` table has TWO uniqueness constraints: the primary key ``id``
and a natural-key unique index over ``(flow_run_id, task_key, dynamic_key)``.
When the recorder resolves only ``id`` conflicts, two events describing the
SAME logical run (same natural key) but carrying DIFFERENT ids collide on the
natural-key index and raise ``IntegrityError`` — the task run is lost or left
stale. A correct fix reconciles BOTH constraints into a single surviving run
carrying the latest state, with state-history rows attributed to the surviving
canonical id. The tests below assert that contract, which is why they check
survivor identity and state-history attribution rather than any internal
reconciliation mechanism.

The verifier couples only to pre-existing, stable public symbols
(``record_bulk_task_run_events`` plus the ``read_task_run`` /
``read_task_run_states`` read-backs), so any correct fix passes regardless of
how it groups or coalesces conflicts.

Fixtures are self-contained because the repo's test conftest is not in scope
when this file is collected from /tests/verify/; they mirror the repo's
``session`` / ``flow_run`` fixtures. The in-memory SQLite database is shared
across sessions via the ``provide_database_interface()`` singleton, exactly as
the repo's own recorder tests rely on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from prefect.server import models, schemas
from prefect.server.database import provide_database_interface
from prefect.server.events.schemas.events import ReceivedEvent
from prefect.server.models.task_run_states import read_task_run_states
from prefect.server.models.task_runs import read_task_run
from prefect.server.schemas.states import StateType
from prefect.server.services import task_run_recorder


@pytest.fixture
async def session():
    """An AsyncSession bound to the configured test database.

    Test-mode env (PREFECT_TESTING_TEST_MODE / UNIT_TEST_MODE, set by
    tests/test-setup.sh) selects the in-memory SQLite backend. The recorder
    opens its OWN session via ``provide_database_interface()`` and commits;
    because the engine is cached on the singleton interface, both sessions
    observe the same database. Call ``session.expire_all()`` before reading
    back data the recorder committed on its own session.
    """
    db = provide_database_interface()
    await db.create_db()
    s = await db.session()
    async with s:
        yield s


@pytest.fixture
async def flow_run(session: AsyncSession):
    """A freshly-created flow run with a unique flow_run id per test.

    A unique flow_run id keeps each test's natural key
    ``(flow_run_id, task_key, dynamic_key)`` distinct, so the shared
    in-memory database carries no cross-test natural-key collisions.
    """
    flow = await models.flows.create_flow(
        session=session,
        flow=schemas.core.Flow(name=f"verify-flow-{uuid4()}"),
    )
    await session.commit()
    fr = await models.flow_runs.create_flow_run(
        session=session,
        flow_run=schemas.core.FlowRun(flow_id=flow.id, flow_version="0.1"),
    )
    await session.commit()
    return fr


def make_event_with_flow_run(
    task_run_id: str,
    flow_run_id: str,
    task_key: str,
    dynamic_key: str,
    state_ts: datetime,
    state_type: StateType = StateType.RUNNING,
) -> ReceivedEvent:
    """Build a client-orchestrated task-run event tied to a flow run.

    Matches what the recorder parses in ``task_run_from_event``: the task-run
    id lives in the resource id, the flow run is a related resource in the
    ``flow-run`` role, and the natural-key fields (task_key, dynamic_key) plus
    the validated state live in the payload.
    """
    state_ts_str = state_ts.isoformat()
    return ReceivedEvent(
        occurred=state_ts_str,
        event=f"prefect.task-run.{state_type.name.title()}",
        resource={
            "prefect.resource.id": f"prefect.task-run.{task_run_id}",
            "prefect.resource.name": "test-task-run",
            "prefect.state-message": "",
            "prefect.state-name": state_type.name.title(),
            "prefect.state-timestamp": state_ts_str,
            "prefect.state-type": state_type.name,
            "prefect.orchestration": "client",
        },
        related=[
            {
                "prefect.resource.id": f"prefect.flow-run.{flow_run_id}",
                "prefect.resource.role": "flow-run",
            },
        ],
        payload={
            "intended": {"from": "PENDING", "to": state_type.name},
            "validated_state": {
                "type": state_type.name,
                "name": state_type.name.title(),
                "message": "",
            },
            "task_run": {
                "name": "test-task-run",
                "task_key": task_key,
                "dynamic_key": dynamic_key,
            },
        },
        account=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        workspace=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        received=state_ts_str,
        id=uuid4(),
        follows=None,
    )


def make_event_without_flow_run(
    task_run_id: str,
    task_key: str,
    dynamic_key: str,
    state_ts: datetime,
    state_type: StateType = StateType.RUNNING,
) -> ReceivedEvent:
    """Build a task-run event with NO related flow run.

    Without a flow run id the natural key is not enforceable, so the recorder
    falls back to id-based upsert behavior.
    """
    state_ts_str = state_ts.isoformat()
    return ReceivedEvent(
        occurred=state_ts_str,
        event=f"prefect.task-run.{state_type.name.title()}",
        resource={
            "prefect.resource.id": f"prefect.task-run.{task_run_id}",
            "prefect.resource.name": "test-task-run",
            "prefect.state-message": "",
            "prefect.state-name": state_type.name.title(),
            "prefect.state-timestamp": state_ts_str,
            "prefect.state-type": state_type.name,
            "prefect.orchestration": "client",
        },
        related=[],
        payload={
            "intended": {"from": "PENDING", "to": state_type.name},
            "validated_state": {
                "type": state_type.name,
                "name": state_type.name.title(),
                "message": "",
            },
            "task_run": {
                "name": "test-task-run",
                "task_key": task_key,
                "dynamic_key": dynamic_key,
            },
        },
        account=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        workspace=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        received=state_ts_str,
        id=uuid4(),
        follows=None,
    )


async def test_sequential_natural_key_conflict_updates_existing(
    session: AsyncSession, flow_run
):
    """Two events for the SAME logical run (same flow_run_id/task_key/
    dynamic_key) but DIFFERENT ids, recorded in two SEPARATE recorder calls
    with increasing state timestamps, must reconcile into one surviving run at
    the LATEST state — not raise IntegrityError and lose the run.

    Pre-fix this fails: the second call raises
    ``sqlite3.IntegrityError: UNIQUE constraint failed:
    task_run.flow_run_id, task_run.task_key, task_run.dynamic_key``.
    """
    flow_run_id = str(flow_run.id)
    task_key, dynamic_key = "my_task-abcdefg", "3"
    base = datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    first_id, second_id = str(uuid4()), str(uuid4())

    await task_run_recorder.record_bulk_task_run_events(
        [
            make_event_with_flow_run(
                first_id, flow_run_id, task_key, dynamic_key, base, StateType.PENDING
            )
        ]
    )
    await task_run_recorder.record_bulk_task_run_events(
        [
            make_event_with_flow_run(
                second_id,
                flow_run_id,
                task_key,
                dynamic_key,
                base + timedelta(minutes=1),
                StateType.RUNNING,
            )
        ]
    )

    session.expire_all()
    surviving = await read_task_run(session=session, task_run_id=first_id)
    assert surviving is not None, "the first (existing) task run must survive"
    assert surviving.state_type == StateType.RUNNING, (
        "surviving run must reflect the LATEST state, not the stale one"
    )

    duplicate = await read_task_run(session=session, task_run_id=second_id)
    assert duplicate is None, "the conflicting id must not create a second run"

    states = await read_task_run_states(session, surviving.id)
    assert [s.type for s in states] == [StateType.PENDING, StateType.RUNNING]
    assert {s.task_run_id for s in states} == {surviving.id}
    assert {s.state_details.task_run_id for s in states} == {surviving.id}


async def test_same_batch_natural_key_coalesced(session: AsyncSession, flow_run):
    """Two events for the SAME logical run (same natural key, different ids,
    increasing timestamps) delivered WITHIN A SINGLE batch must be coalesced
    into one surviving run at the latest state, rather than triggering a
    natural-key IntegrityError that loses the whole batch.

    The test is agnostic about WHICH of the two freshly-minted client ids
    becomes the canonical survivor — a fix may coalesce onto the earliest or
    the latest incoming id. It asserts only the contract: exactly one run
    survives for the natural key, it carries the LATEST state, and its full
    state history is attributed to the surviving id.

    Pre-fix this fails (batch-level natural-key collision).
    """
    flow_run_id = str(flow_run.id)
    task_key, dynamic_key = "batch_task-x", "7"
    base = datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    first_id, second_id = str(uuid4()), str(uuid4())

    await task_run_recorder.record_bulk_task_run_events(
        [
            make_event_with_flow_run(
                first_id, flow_run_id, task_key, dynamic_key, base, StateType.PENDING
            ),
            make_event_with_flow_run(
                second_id,
                flow_run_id,
                task_key,
                dynamic_key,
                base + timedelta(minutes=1),
                StateType.RUNNING,
            ),
        ]
    )

    session.expire_all()
    # The fix may coalesce onto EITHER incoming id; exactly one must survive.
    by_id = {
        first_id: await read_task_run(session=session, task_run_id=first_id),
        second_id: await read_task_run(session=session, task_run_id=second_id),
    }
    survivors = [tr for tr in by_id.values() if tr is not None]
    assert len(survivors) == 1, (
        "exactly one run must survive the same-batch natural-key collision, "
        f"got {len(survivors)}"
    )
    surviving = survivors[0]
    assert surviving.state_type == StateType.RUNNING, (
        "surviving run must reflect the LATEST state in the batch"
    )

    states = await read_task_run_states(session, surviving.id)
    assert [s.type for s in states] == [StateType.PENDING, StateType.RUNNING]
    assert {s.task_run_id for s in states} == {surviving.id}
    assert {s.state_details.task_run_id for s in states} == {surviving.id}


async def test_id_conflict_updates_existing(session: AsyncSession, flow_run):
    """The pre-existing behavior for repeated events sharing the SAME task-run
    id must be preserved: the later event (by state timestamp) wins, updating
    the run's recorded fields and state. This path already worked pre-fix and
    must not regress.
    """
    flow_run_id = str(flow_run.id)
    task_run_id = str(uuid4())
    base = datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)

    await task_run_recorder.record_bulk_task_run_events(
        [
            make_event_with_flow_run(
                task_run_id,
                flow_run_id,
                "old-task-key",
                "old-dynamic-key",
                base,
                StateType.PENDING,
            )
        ]
    )
    await task_run_recorder.record_bulk_task_run_events(
        [
            make_event_with_flow_run(
                task_run_id,
                flow_run_id,
                "new-task-key",
                "new-dynamic-key",
                base + timedelta(minutes=1),
                StateType.RUNNING,
            )
        ]
    )

    session.expire_all()
    tr = await read_task_run(session=session, task_run_id=task_run_id)
    assert tr is not None
    assert tr.task_key == "new-task-key"
    assert tr.dynamic_key == "new-dynamic-key"
    assert tr.state_type == StateType.RUNNING

    states = await read_task_run_states(session, tr.id)
    assert [s.type for s in states] == [StateType.PENDING, StateType.RUNNING]
    assert {s.state_details.task_run_id for s in states} == {tr.id}


async def test_no_flow_run_id_upserts_by_id(session: AsyncSession):
    """Task runs WITHOUT a flow run id (natural key not enforceable) must still
    be recorded and updated by id. The fix must not break this fallback path.
    """
    task_run_id = str(uuid4())
    base = datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)

    await task_run_recorder.record_bulk_task_run_events(
        [
            make_event_without_flow_run(
                task_run_id, "lonely-task", "0", base, StateType.PENDING
            )
        ]
    )
    await task_run_recorder.record_bulk_task_run_events(
        [
            make_event_without_flow_run(
                task_run_id,
                "lonely-task",
                "0",
                base + timedelta(minutes=1),
                StateType.RUNNING,
            )
        ]
    )

    session.expire_all()
    tr = await read_task_run(session=session, task_run_id=task_run_id)
    assert tr is not None
    assert tr.flow_run_id is None
    assert tr.state_type == StateType.RUNNING

    states = await read_task_run_states(session, tr.id)
    assert [s.type for s in states] == [StateType.PENDING, StateType.RUNNING]
    assert {s.state_details.task_run_id for s in states} == {tr.id}
