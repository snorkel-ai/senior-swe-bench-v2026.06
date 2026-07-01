"""Validation test harness for paperless-ngx-refactor-task-system.

Provides ONLY framework mechanics — no assertions, no implementation
discovery by implementation-invented symbol name:

- Django bootstrap (env vars, sys.path, ``django.setup()``) for any
  subprocess driver. Pytest stories run under pytest-django and don't
  need it.
- Authenticated DRF ``APIClient`` factories with Accept-header API
  versioning (v9 vs v10) and model-level Permission grants.
- Guardian object-level permission helper (``grant_object_perm``).
- A ``Document`` factory wrapper for the duplicate-documents story.
- Builders for the inputs a tracked Celery ``consume_file`` task carries
  (a consumable-document stand-in and a real ``DocumentMetadataOverrides``).
- Drivers for Celery's PUBLIC task-lifecycle signals
  (``before_task_publish`` / ``task_prerun`` / ``task_postrun`` /
  ``task_failure`` / ``task_revoked``).

WHY signals + HTTP only: the ``PaperlessTask`` rows are created and
mutated exclusively by the agent's signal handlers (whatever they are
named), and observed through the version-stable ``/api/tasks/`` HTTP
surface. Nothing here imports the agent's handler functions, helper
functions, the tracked-task mapping, the result TypedDicts, or any
internal model field name. A valid implementation that renames model
fields, handlers, or helpers still passes — the public Celery signal
contract drives state in, and the public API response contract reads it
out.

Story procedures import this module via::

    import sys
    sys.path.insert(0, "/tests/validate")
    from test_harness import (
        api_client, make_user, grant_object_perm, make_document,
        make_consumable_document, make_overrides,
        publish_task, start_task, finish_task, fail_task, revoke_task,
        CONSUME_FILE_TASK, TRAIN_CLASSIFIER_TASK, SANITY_CHECK_TASK,
        LLM_INDEX_TASK, MAIL_FETCH_TASK, UNTRACKED_TASK,
        ACCEPT_V9, ACCEPT_V10,
    )
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from types import SimpleNamespace
from typing import Iterable

REPO_DIR = "/repo/paperless-ngx"

# Accept headers for the repo's pre-existing API versioning machinery.
# DEFAULT_VERSION="10", ALLOWED_VERSIONS=["9","10"] are configured in the
# base repo; this task only adds a v9-vs-v10 serializer split.
ACCEPT_V9 = "application/json; version=9"
ACCEPT_V10 = "application/json; version=10"

# Real Celery task names the worker dispatches. These are PRE-EXISTING task
# paths (registered @shared_task names), not symbols this task invents. Any
# correct implementation must recognise these same names to track them.
CONSUME_FILE_TASK = "documents.tasks.consume_file"
TRAIN_CLASSIFIER_TASK = "documents.tasks.train_classifier"
SANITY_CHECK_TASK = "documents.tasks.sanity_check"
LLM_INDEX_TASK = "documents.tasks.llmindex_index"
MAIL_FETCH_TASK = "paperless_mail.tasks.process_mail_accounts"
# A task name that is NOT one of the tracked background jobs — used to prove
# untracked tasks are ignored by the publish handler.
UNTRACKED_TASK = "celery.backend_cleanup"


# ---------------------------------------------------------------------------
# Django bootstrap — subprocess drivers only. Pytest stories use pytest-django.
# ---------------------------------------------------------------------------


def setup_django() -> None:
    """Configure environment + sys.path and call ``django.setup()`` once."""
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

    src_path = f"{REPO_DIR}/src"
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    import django  # noqa: PLC0415

    django.setup()


def safe_run(fn) -> None:  # type: ignore[no-untyped-def]
    """Invoke ``fn``, print its return value as JSON; structured error on raise."""
    try:
        result = fn()
        print(json.dumps(result))
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                },
            ),
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Users / clients / permissions.
# ---------------------------------------------------------------------------


def make_user(
    username: str,
    *,
    is_superuser: bool = False,
    model_perms: Iterable[str] | None = None,
):
    """Create a User, optionally granting model-level permissions by codename."""
    from django.contrib.auth.models import Permission, User  # noqa: PLC0415

    if is_superuser:
        user = User.objects.create_superuser(username=username)
    else:
        user = User.objects.create_user(username=username)

    if model_perms:
        perms = list(Permission.objects.filter(codename__in=list(model_perms)))
        user.user_permissions.add(*perms)
        # Reload so the permission cache is fresh for subsequent checks.
        user = User.objects.get(pk=user.pk)
    return user


def api_client(user=None, *, version: int | None = 10):  # type: ignore[no-untyped-def]
    """Return a DRF ``APIClient`` authenticated as ``user`` at API ``version``.

    ``version`` selects the Accept header the repo's versioning negotiates
    on: 9 → the legacy task shape, 10 → the new structured shape. Pass
    ``version=None`` to send no explicit Accept header (server default).
    """
    from rest_framework.test import APIClient  # noqa: PLC0415

    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    if version is not None:
        accept = ACCEPT_V9 if int(version) < 10 else ACCEPT_V10
        client.credentials(HTTP_ACCEPT=accept)
    return client


def grant_object_perm(perm_codename: str, user, obj) -> None:  # type: ignore[no-untyped-def]
    """Assign a Guardian object-level permission (``assign_perm`` re-export)."""
    from guardian.shortcuts import assign_perm  # noqa: PLC0415

    assign_perm(perm_codename, user, obj)


# ---------------------------------------------------------------------------
# Document factory wrapper — for the v9 duplicate-documents story.
# ---------------------------------------------------------------------------


def make_document(*, owner=None, title: str = "dup-doc", checksum: str | None = None):
    """Create a real ``Document`` via the repo's pre-existing DocumentFactory."""
    from documents.tests.factories import DocumentFactory  # noqa: PLC0415

    return DocumentFactory.create(
        title=title,
        owner=owner,
        checksum=checksum or uuid.uuid4().hex,
        mime_type="application/pdf",
    )


# ---------------------------------------------------------------------------
# consume_file task-input builders.
# ---------------------------------------------------------------------------


def make_consumable_document(
    *,
    filename: str = "invoice.pdf",
    mime_type: str = "application/pdf",
    original_path=None,
    mailrule_id=None,
):
    """Build a stand-in for the consumable-document input a consume task carries.

    The publish handler reads ``input_doc.original_file.name``,
    ``input_doc.mime_type``, ``input_doc.original_path`` and
    ``input_doc.mailrule_id`` (attribute access only), so a SimpleNamespace
    is sufficient and avoids constructing the heavyweight real object.
    """
    return SimpleNamespace(
        original_file=SimpleNamespace(name=filename),
        mime_type=mime_type,
        original_path=original_path,
        mailrule_id=mailrule_id,
    )


def make_overrides(**kwargs):
    """Return a real ``DocumentMetadataOverrides`` carrying the given fields.

    The publish handler serialises ``vars(overrides)`` (skipping None and
    underscore-prefixed entries, ISO-formatting dates and stringifying
    Paths), and reads ``overrides.owner_id`` for ownership — so this MUST be
    the real dataclass, not a mock (``vars()`` on a MagicMock is meaningless).
    """
    from documents.data_models import DocumentMetadataOverrides  # noqa: PLC0415

    overrides = DocumentMetadataOverrides()
    for key, value in kwargs.items():
        setattr(overrides, key, value)
    return overrides


# ---------------------------------------------------------------------------
# Celery PUBLIC lifecycle-signal drivers.
#
# These dispatch Celery's documented signals exactly as the worker would.
# The agent's connected handlers (whatever they are named) react and
# create/transition the PaperlessTask row. We never import those handlers.
# ---------------------------------------------------------------------------


def new_task_id() -> str:
    """Return a fresh unique Celery task id."""
    return uuid.uuid4().hex


def publish_task(
    task_name: str,
    *,
    task_id: str,
    trigger_source: str | None = None,
    args: tuple | None = None,
    kwargs: dict | None = None,
    extra_headers: dict | None = None,
) -> None:
    """Fire ``before_task_publish`` for a task being queued.

    Mirrors Celery's protocol-v2 message: ``headers`` carries ``task`` and
    ``id`` (plus any ``trigger_source`` the dispatcher attached), and
    ``body`` is the ``(args, kwargs, embed)`` 3-tuple.
    """
    from celery.signals import before_task_publish  # noqa: PLC0415

    headers = {"task": task_name, "id": task_id}
    if trigger_source is not None:
        headers["trigger_source"] = trigger_source
    if extra_headers:
        headers.update(extra_headers)
    body = (tuple(args or ()), dict(kwargs or {}), {})
    before_task_publish.send(sender=task_name, headers=headers, body=body)


def start_task(task_id: str) -> None:
    """Fire ``task_prerun`` (worker picked the task up)."""
    from celery.signals import task_prerun  # noqa: PLC0415

    # task=None: the handler's tracked-task guard short-circuits on a falsy
    # task and proceeds to update the row keyed by task_id (which only exists
    # if it was published as a tracked task).
    task_prerun.send(sender=task_id, task_id=task_id, task=None)


def finish_task(task_id: str, *, state: str = "SUCCESS", retval=None) -> None:
    """Fire ``task_postrun`` with a terminal Celery ``state`` and return value."""
    from celery.signals import task_postrun  # noqa: PLC0415

    task_postrun.send(
        sender=task_id,
        task_id=task_id,
        task=None,
        retval=retval,
        state=state,
    )


def fail_task(task_id: str, *, exception) -> None:  # type: ignore[no-untyped-def]
    """Fire ``task_failure`` carrying the raised exception."""
    from celery.signals import task_failure  # noqa: PLC0415

    task_failure.send(
        sender=None,
        task_id=task_id,
        exception=exception,
        traceback=None,
        einfo=None,
    )


def revoke_task(task_id: str) -> None:
    """Fire ``task_revoked`` for a cancelled task (request carries the id)."""
    from celery.signals import task_revoked  # noqa: PLC0415

    request = SimpleNamespace(id=task_id)
    task_revoked.send(sender=None, request=request)
