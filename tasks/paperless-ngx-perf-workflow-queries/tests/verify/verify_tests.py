"""Behavioral verifier for paperless-ngx-perf-workflow-queries.

Measures SQL query counts (deterministic on SQLite) through the two
pre-existing stable HTTP routes, plus correctness guards on action
ordering and response shape:

  GET   /api/workflows/        — list workflows (+ nested triggers/actions
                                 and their ManyToMany sub-fields)
  PATCH /api/workflows/{id}/   — save a workflow (runs orphan cleanup of
                                 unattached triggers/actions)
"""

from __future__ import annotations

import os
import sys

# Repo-notes Gotcha #8: PAPERLESS_* env vars MUST be set before Django import.
# Most are set by the Dockerfile ENV; setdefault is defensive for clean envs.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paperless.settings")
os.environ.setdefault("PAPERLESS_SECRET_KEY", "test-secret-key-benchmark")
os.environ.setdefault("PAPERLESS_DATA_DIR", "/tmp/paperless-data")
os.environ.setdefault("PAPERLESS_MEDIA_ROOT", "/tmp/paperless-media")
os.environ.setdefault("PAPERLESS_CONSUMPTION_DIR", "/tmp/paperless-consume")
os.environ.setdefault("PAPERLESS_REDIS", "redis://localhost:6379")
os.environ.setdefault("PAPERLESS_DISABLE_DBHANDLER", "true")
os.environ.setdefault(
    "PAPERLESS_CACHE_BACKEND",
    "django.core.cache.backends.locmem.LocMemCache",
)
os.environ.setdefault(
    "PAPERLESS_CHANNELS_BACKEND",
    "channels.layers.InMemoryChannelLayer",
)

# Repo-notes Gotcha #14: paperless-ngx packages live under src/.
if "/repo/paperless-ngx/src" not in sys.path:
    sys.path.insert(0, "/repo/paperless-ngx/src")

import django  # noqa: E402

django.setup()

import pytest  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.db import connection  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

from documents.models import Correspondent  # noqa: E402
from documents.models import Tag  # noqa: E402
from documents.models import Workflow  # noqa: E402
from documents.models import WorkflowAction  # noqa: E402
from documents.models import WorkflowTrigger  # noqa: E402

ENDPOINT = "/api/workflows/"

# Keep every workflow on one page so the query count is stable.
# StandardPagination.max_page_size is well above this.
WIDE_PAGE_SIZE = 200

# ---------------------------------------------------------------------------
# Query budgets — calibrated empirically against the pre-fix and post-fix
# trees inside the built image (SQLite, query counts via
# CaptureQueriesContext):
#
#   GET /api/workflows/  (8 workflows w/ trigger+action M2M):
#       pre-fix ~340 queries     post-fix ~29 queries
#   PATCH /api/workflows/{id}/  (40 orphan triggers + 40 orphan actions):
#       pre-fix ~1253 queries    post-fix ~41 queries
#
# Budgets sit comfortably between the two: post-fix has ~3x headroom under
# the budget, pre-fix is multiples OVER it.
# ---------------------------------------------------------------------------
LIST_FIXTURE_WORKFLOWS = 8
LIST_QUERY_BUDGET = 100  # post-fix ~29 ; pre-fix ~340

PRUNE_ORPHAN_TRIGGERS = 40
PRUNE_ORPHAN_ACTIONS = 40
PRUNE_QUERY_BUDGET = 120  # post-fix ~41 ; pre-fix ~1253


def _superuser_client(username: str) -> APIClient:
    user = User.objects.create_superuser(username=username)
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _make_trigger(idx: int, tags, correspondents) -> WorkflowTrigger:
    """A trigger with a couple of its ManyToMany filter relations populated.

    Populating nested M2M fields is what surfaces the N+1: prefetching only
    the `triggers` relation (not its sub-relations) still lazy-loads these.
    """
    trigger = WorkflowTrigger.objects.create(
        type=WorkflowTrigger.WorkflowTriggerType.CONSUMPTION,
        filter_filename=f"*{idx}*",
    )
    for tag in tags:
        trigger.filter_has_tags.add(tag)
    for corr in correspondents:
        trigger.filter_has_any_correspondents.add(corr)
    return trigger


def _make_action(title: str, order: int, tags) -> WorkflowAction:
    action = WorkflowAction.objects.create(assign_title=title, order=order)
    for tag in tags:
        action.assign_tags.add(tag)
    return action


# ---------------------------------------------------------------------------
# T1 — GET list query count is bounded (does not scale with nested M2M data)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_list_query_count_bounded():
    client = _superuser_client("verify_list_admin")

    tags = [Tag.objects.create(name=f"list-tag-{i}") for i in range(3)]
    correspondents = [
        Correspondent.objects.create(name=f"list-corr-{i}") for i in range(2)
    ]

    for w in range(LIST_FIXTURE_WORKFLOWS):
        trigger = _make_trigger(w, tags, correspondents)
        action = _make_action(f"action-{w}", order=0, tags=tags)
        workflow = Workflow.objects.create(name=f"wf-{w}", order=w)
        workflow.triggers.add(trigger)
        workflow.actions.add(action)

    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(ENDPOINT, {"page_size": WIDE_PAGE_SIZE}, format="json")

    assert resp.status_code == 200, resp.status_code
    assert resp.data["count"] == LIST_FIXTURE_WORKFLOWS
    n_queries = len(ctx)
    assert n_queries <= LIST_QUERY_BUDGET, (
        f"GET {ENDPOINT} issued {n_queries} queries for "
        f"{LIST_FIXTURE_WORKFLOWS} workflows (budget {LIST_QUERY_BUDGET}). "
        "The nested trigger/action ManyToMany relations are not being "
        "prefetched — this is the N+1 the optimization must eliminate."
    )


# ---------------------------------------------------------------------------
# T2 — PATCH orphan cleanup query count is bounded (set-based, not per-object)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_prune_query_count_bounded():
    client = _superuser_client("verify_prune_admin")

    # Orphaned triggers/actions — attached to NO workflow. The save-time
    # cleanup must discard these; pre-fix it runs one count query per object.
    for i in range(PRUNE_ORPHAN_TRIGGERS):
        WorkflowTrigger.objects.create(
            type=WorkflowTrigger.WorkflowTriggerType.CONSUMPTION,
            filter_filename=f"orphan-trigger-{i}",
        )
    for i in range(PRUNE_ORPHAN_ACTIONS):
        WorkflowAction.objects.create(assign_title=f"orphan-action-{i}", order=0)

    workflow = Workflow.objects.create(name="prune-wf", order=0)

    with CaptureQueriesContext(connection) as ctx:
        resp = client.patch(
            f"{ENDPOINT}{workflow.id}/",
            {"name": "prune-wf-renamed"},
            format="json",
        )

    assert resp.status_code == 200, (resp.status_code, getattr(resp, "data", None))

    # Cleanup happened: the orphaned triggers/actions are gone (only those
    # still attached to a workflow remain — and this workflow has none).
    assert WorkflowTrigger.objects.count() == 0
    assert WorkflowAction.objects.count() == 0

    n_queries = len(ctx)
    assert n_queries <= PRUNE_QUERY_BUDGET, (
        f"PATCH {ENDPOINT}{{id}}/ issued {n_queries} queries with "
        f"{PRUNE_ORPHAN_TRIGGERS} orphan triggers + {PRUNE_ORPHAN_ACTIONS} "
        f"orphan actions (budget {PRUNE_QUERY_BUDGET}). The orphan cleanup "
        "is running a count query per object instead of a set-based delete."
    )


# ---------------------------------------------------------------------------
# T2b — prune only removes orphans; rows still attached to a workflow survive
# (pass_to_pass guard against a fast-but-wrong delete-everything prune)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_prune_preserves_attached():
    client = _superuser_client("verify_prune_scope_admin")

    # Orphans — attached to no workflow; the save-time cleanup must remove them.
    for i in range(PRUNE_ORPHAN_TRIGGERS):
        WorkflowTrigger.objects.create(
            type=WorkflowTrigger.WorkflowTriggerType.CONSUMPTION,
            filter_filename=f"orphan-trigger-{i}",
        )
    for i in range(PRUNE_ORPHAN_ACTIONS):
        WorkflowAction.objects.create(assign_title=f"orphan-action-{i}", order=0)

    # A second workflow with an ATTACHED trigger + action. These are not
    # orphans and must survive the prune — deleting them is a caller-visible
    # behavior change. A too-coarse `.all().delete()` prune (fast, but wrong)
    # destroys them and is rejected here.
    attached_trigger = WorkflowTrigger.objects.create(
        type=WorkflowTrigger.WorkflowTriggerType.CONSUMPTION,
        filter_filename="attached-trigger",
    )
    attached_action = WorkflowAction.objects.create(
        assign_title="attached-action", order=0
    )
    other_workflow = Workflow.objects.create(name="other-wf", order=1)
    other_workflow.triggers.add(attached_trigger)
    other_workflow.actions.add(attached_action)

    # Patch an unrelated workflow to trigger the save-time orphan prune.
    workflow = Workflow.objects.create(name="prune-scope-wf", order=0)
    resp = client.patch(
        f"{ENDPOINT}{workflow.id}/",
        {"name": "prune-scope-wf-renamed"},
        format="json",
    )
    assert resp.status_code == 200, (resp.status_code, getattr(resp, "data", None))

    # The attached trigger/action on the other workflow survive...
    assert WorkflowTrigger.objects.filter(pk=attached_trigger.pk).exists(), (
        "prune deleted a trigger still attached to another workflow — orphan "
        "cleanup must only remove triggers attached to no workflow"
    )
    assert WorkflowAction.objects.filter(pk=attached_action.pk).exists(), (
        "prune deleted an action still attached to another workflow — orphan "
        "cleanup must only remove actions attached to no workflow"
    )
    # ...and every orphan is gone (nothing remains except the attached pair).
    assert WorkflowTrigger.objects.exclude(pk=attached_trigger.pk).count() == 0, (
        "orphan triggers were not pruned"
    )
    assert WorkflowAction.objects.exclude(pk=attached_action.pk).count() == 0, (
        "orphan actions were not pruned"
    )


# ---------------------------------------------------------------------------
# T3 — actions stay ordered by `order` (guards a fast-but-wrong solution)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_actions_returned_in_order():
    client = _superuser_client("verify_order_admin")

    workflow = Workflow.objects.create(name="order-wf", order=0)
    # Create the order=1 action FIRST so it gets the lower primary key — this
    # makes pk order DISAGREE with `order`, so a solution that falls back to
    # default (pk) ordering will visibly fail.
    action_second = WorkflowAction.objects.create(assign_title="second", order=1)
    action_first = WorkflowAction.objects.create(assign_title="first", order=0)
    workflow.actions.add(action_second)
    workflow.actions.add(action_first)

    resp = client.get(ENDPOINT, {"page_size": WIDE_PAGE_SIZE}, format="json")
    assert resp.status_code == 200, resp.status_code

    workflow_row = next(
        row for row in resp.data["results"] if row["id"] == workflow.id
    )
    action_ids = [a["id"] for a in workflow_row["actions"]]
    assert action_first.id in action_ids and action_second.id in action_ids
    assert action_ids.index(action_first.id) < action_ids.index(action_second.id), (
        f"actions returned in order {action_ids}; expected the order=0 action "
        f"(id={action_first.id}) before the order=1 action (id={action_second.id}). "
        "Action ordering was not preserved by the optimization."
    )


# ---------------------------------------------------------------------------
# T4 — response shape/values unchanged (pass_to_pass regression guard)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_list_response_shape_unchanged():
    client = _superuser_client("verify_shape_admin")

    tag_a = Tag.objects.create(name="shape-tag-a")
    tag_b = Tag.objects.create(name="shape-tag-b")

    trigger = WorkflowTrigger.objects.create(
        type=WorkflowTrigger.WorkflowTriggerType.CONSUMPTION,
        filter_filename="*shape*",
    )
    trigger.filter_has_tags.add(tag_a)
    trigger.filter_has_tags.add(tag_b)

    action_first = WorkflowAction.objects.create(assign_title="shape-1", order=0)
    action_first.assign_tags.add(tag_a)
    action_second = WorkflowAction.objects.create(assign_title="shape-2", order=1)

    workflow = Workflow.objects.create(name="shape-wf", order=0)
    workflow.triggers.add(trigger)
    workflow.actions.add(action_first)
    workflow.actions.add(action_second)

    resp = client.get(ENDPOINT, {"page_size": WIDE_PAGE_SIZE}, format="json")
    assert resp.status_code == 200, resp.status_code

    workflow_row = next(
        row for row in resp.data["results"] if row["id"] == workflow.id
    )

    # Same workflow with its trigger and both actions, nested values intact.
    assert workflow_row["name"] == "shape-wf"
    assert len(workflow_row["triggers"]) == 1
    assert len(workflow_row["actions"]) == 2

    trigger_row = workflow_row["triggers"][0]
    assert set(trigger_row["filter_has_tags"]) == {tag_a.id, tag_b.id}

    returned_action_ids = {a["id"] for a in workflow_row["actions"]}
    assert returned_action_ids == {action_first.id, action_second.id}

    # The action that had a tag still carries it through serialization.
    first_row = next(
        a for a in workflow_row["actions"] if a["id"] == action_first.id
    )
    assert set(first_row["assign_tags"]) == {tag_a.id}
