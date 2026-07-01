"""Tests couple only to pre-existing public symbols
(`prefect.server.events.{triggers,actions}`,
`prefect.server.events.models.automations`,
`prefect.server.events.schemas.{automations,events}`); they import no
reference-only helper, so any locking strategy (SELECT FOR UPDATE row
locks, UPDATE...RETURNING claim columns, unique-index idempotency keys,
application-layer asyncio mutexes) passes without modification.

The test database is pre-pointed at PostgreSQL by tests/test-setup.sh;
SQLite suppresses the bug because it serialises writes at the file level.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta
from typing import Tuple
from unittest import mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from prefect.server.events import actions, triggers
from prefect.server.events.models import automations
from prefect.server.events.schemas.automations import (
    Automation,
    CompoundTrigger,
    EventTrigger,
    Posture,
)
from prefect.server.events.schemas.events import ReceivedEvent


@pytest.fixture
async def db_session():
    """In-process AsyncSession bound to whatever database the env points
    at (PostgreSQL, per tests/test-setup.sh).

    `db.create_db()` is idempotent — it runs Alembic migrations through
    `head` and is a no-op when the schema is already current.
    """
    from prefect.server.database import provide_database_interface

    db = provide_database_interface()
    await db.create_db()
    session = await db.session()
    async with session:
        yield session


@pytest.fixture
def act(monkeypatch: pytest.MonkeyPatch) -> mock.AsyncMock:
    """Replace the parent-action publisher with an AsyncMock so the
    verifier can count parent-fire invocations directly.

    `triggers.act` is the public callable that
    `evaluate_composite_trigger` invokes when a parent compound trigger
    fires. Monkeypatching it gives a precise call-count signal that does
    not depend on any task-introduced symbol. The same pattern is used
    by Prefect's own test suite (see
    tests/events/server/triggers/test_composite_triggers.py).
    """
    m = mock.AsyncMock()
    monkeypatch.setattr("prefect.server.events.triggers.act", m)
    return m


async def _make_compound_automation(
    session: AsyncSession,
    *,
    name: str,
    require,
) -> Automation:
    """Create + persist + register a CompoundTrigger automation with two
    EventTrigger children expecting `event.A` and `event.B` respectively.

    Uses only pre-existing public APIs:
    - `automations.create_automation(session=, automation=)` — persists
      to the DB (db is auto-injected by @db_injector).
    - `triggers.load_automation(automation)` — registers in the in-memory
      trigger lookup so `reactive_evaluation` can match incoming events.
    """
    automation = Automation(
        name=name,
        trigger=CompoundTrigger(
            require=require,
            within=timedelta(minutes=5),
            triggers=[
                EventTrigger(
                    expect={"event.A"},
                    match={"prefect.resource.id": "*"},
                    posture=Posture.Reactive,
                    threshold=1,
                ),
                EventTrigger(
                    expect={"event.B"},
                    match={"prefect.resource.id": "*"},
                    posture=Posture.Reactive,
                    threshold=1,
                ),
            ],
        ),
        actions=[actions.DoNothing()],
    )

    persisted = await automations.create_automation(
        session=session, automation=automation
    )
    automation.created = persisted.created
    automation.updated = persisted.updated
    triggers.load_automation(persisted)
    await session.commit()
    return automation


def _event(name: str, *, resource_id: str, occurred) -> ReceivedEvent:
    """Build a ReceivedEvent matching the trigger's resource match
    (`{"prefect.resource.id": "*"}`)."""
    return ReceivedEvent(
        occurred=occurred,
        event=name,
        resource={"prefect.resource.id": resource_id},
        id=uuid4(),
    )


@pytest.mark.timeout(360)
async def test_compound_trigger_fires_exactly_once_under_concurrency(
    db_session: AsyncSession, act: mock.AsyncMock
) -> None:
    """The parent of a compound (require="all", two children) trigger must
    fire EXACTLY ONCE when its two child events are processed
    concurrently.

    Iterates N=30 times because the race is non-deterministic. Without
    the fix, on PostgreSQL the never-fire race manifests in roughly
    29/30 trials and the double-fire race occasionally produces
    `count == 2`. With a correct fix, every iteration shows
    `act.call_count == 1`.

    A fresh automation is created per iteration so leftover
    child-firing rows from a previous iteration cannot mask the
    never-fire race.
    """
    N = 30
    counts: list[int] = []

    for i in range(N):
        await triggers.reset()

        automation = await _make_compound_automation(
            db_session, name=f"race-verifier-all-{i}", require="all"
        )

        act.reset_mock()

        now_ts = automation.created + timedelta(seconds=i)
        event_a = _event(
            "event.A", resource_id=f"r{i}", occurred=now_ts + timedelta(microseconds=1)
        )
        event_b = _event(
            "event.B", resource_id=f"r{i}", occurred=now_ts + timedelta(microseconds=2)
        )

        await asyncio.gather(
            triggers.reactive_evaluation(event_a),
            triggers.reactive_evaluation(event_b),
        )

        counts.append(act.call_count)

    bad = sum(1 for c in counts if c != 1)
    distribution = Counter(counts)
    assert bad == 0, (
        f"Compound trigger fired wrong number of times in {bad}/{N} "
        f"iterations. Distribution: {dict(distribution)}. "
        f"count==0 → never-fire race; count>=2 → double-fire race."
    )


@pytest.mark.timeout(120)
async def test_sequential_evaluation_still_fires(
    db_session: AsyncSession, act: mock.AsyncMock
) -> None:
    """Sequential (non-concurrent) child events still cause the
    compound trigger to fire exactly once.

    When events arrive one at a time, each `reactive_evaluation` call
    completes before the next begins, so there is no race to engage; a
    correct fix must not break this standard path.
    """
    await triggers.reset()

    automation = await _make_compound_automation(
        db_session, name="seq-verifier", require="all"
    )

    act.reset_mock()

    now_ts = automation.created + timedelta(seconds=1)
    event_a = _event(
        "event.A", resource_id="seq.r", occurred=now_ts + timedelta(microseconds=1)
    )
    event_b = _event(
        "event.B", resource_id="seq.r", occurred=now_ts + timedelta(microseconds=2)
    )

    # Sequential: process A fully, then B
    await triggers.reactive_evaluation(event_a)
    await triggers.reactive_evaluation(event_b)

    assert act.call_count == 1, (
        f"Sequential compound trigger should fire exactly once, "
        f"got {act.call_count} fires."
    )


@pytest.mark.timeout(120)
async def test_compound_trigger_any_mode_still_works(
    db_session: AsyncSession, act: mock.AsyncMock
) -> None:
    """A compound trigger in `require="any"` mode (num_expected_firings
    == 1) still fires on a single child event.

    Guards the shortcut path in `evaluate_composite_trigger` where a
    single child event is sufficient to fire the parent: a fix for the
    `require="all"` race must not break it.
    """
    await triggers.reset()

    automation = await _make_compound_automation(
        db_session, name="any-mode-verifier", require="any"
    )

    act.reset_mock()

    now_ts = automation.created + timedelta(seconds=1)
    event_a = _event(
        "event.A", resource_id="any.r", occurred=now_ts + timedelta(microseconds=1)
    )

    await triggers.reactive_evaluation(event_a)

    assert act.call_count == 1, (
        f"Compound trigger in 'any' mode should fire once on a single "
        f"child event, got {act.call_count} fires."
    )
