"""Validation test harness — framework mechanics only.

Provides Django bootstrap, authenticated DRF ``APIClient`` factories with
model-level permission grants, and a Guardian ``assign_perm`` re-export.
Does NOT provide assertion helpers or anything that hardcodes task-introduced
symbol names, file paths, or field shapes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

REPO_DIR = Path("/repo/paperless-ngx")
PRE_VISIBILITY_MIGRATION = "0014_alter_paperlesstask_task_name"


def setup_django() -> None:
    """Configure environment + sys.path and call ``django.setup()`` once (idempotent)."""
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

    src_path = str(REPO_DIR / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    import django  # noqa: PLC0415

    django.setup()


def make_user(
    username: str,
    *,
    is_superuser: bool = False,
    model_perms: Iterable[str] | None = None,
):
    """Create a test User, optionally granting model-level permissions by codename.

    Non-superuser users need model-level perms in addition to any Guardian
    object perms. Returns the created ``User`` instance.
    """
    from django.contrib.auth.models import Permission, User  # noqa: PLC0415

    if is_superuser:
        user = User.objects.create_superuser(username=username)
    else:
        user = User.objects.create_user(username=username)

    if model_perms:
        perms = list(Permission.objects.filter(codename__in=list(model_perms)))
        user.user_permissions.add(*perms)
        # Reload so the cached perm cache is fresh on subsequent calls.
        user = User.objects.get(pk=user.pk)
    return user


def api_client(user=None):  # type: ignore[no-untyped-def]
    """Return a DRF ``APIClient`` optionally authenticated as ``user``.

    Does not pin an API version header, so it exercises whatever default the
    implementation ships; a story needing a specific version sets it via
    ``client.credentials(HTTP_ACCEPT=...)``.
    """
    from rest_framework.test import APIClient  # noqa: PLC0415

    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def grant_object_perm(perm_codename: str, user, obj) -> None:  # type: ignore[no-untyped-def]
    """Assign Guardian object-level ``perm_codename`` for ``user`` on ``obj`` (thin ``assign_perm`` re-export)."""
    from guardian.shortcuts import assign_perm  # noqa: PLC0415

    assign_perm(perm_codename, user, obj)


def discover_visibility_migration() -> str:
    """Return the ``documents`` migration that immediately follows
    ``PRE_VISIBILITY_MIGRATION`` in graph order.

    Framework mechanics only: discovers the agent's new migration by position
    in the migration graph — it does NOT assume the migration's name.
    """
    setup_django()
    from django.db.migrations.loader import MigrationLoader  # noqa: PLC0415
    from django.db import connection  # noqa: PLC0415

    loader = MigrationLoader(connection, ignore_no_migrations=True)
    docs = sorted(name for (app, name) in loader.graph.nodes if app == "documents")
    if PRE_VISIBILITY_MIGRATION not in docs:
        raise AssertionError(
            f"pre-visibility leaf {PRE_VISIBILITY_MIGRATION!r} not found in documents "
            f"migrations: {docs[-5:]}"
        )
    later = docs[docs.index(PRE_VISIBILITY_MIGRATION) + 1 :]
    if not later:
        raise AssertionError(
            f"no documents migration after {PRE_VISIBILITY_MIGRATION!r}; the visibility "
            f"migration is missing. Have: {docs[-5:]}"
        )
    return later[0]


def migrate_documents_to(target: str):
    """Migrate the ``documents`` app to ``target`` and return the historical
    ``apps`` registry (use ``apps.get_model(...)`` for state at that migration).

    Framework mechanics wrapping django's ``MigrationExecutor`` so a story can
    build pre-migration state, run the migration, and assert — without pinning
    any task-introduced symbol.
    """
    setup_django()
    from django.db import connection  # noqa: PLC0415
    from django.db.migrations.executor import MigrationExecutor  # noqa: PLC0415

    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([("documents", target)])
    executor.loader.build_graph()
    return executor.loader.project_state(("documents", target)).apps


def run_jest_test_pattern(
    pattern: str,
    *,
    cwd: str | Path = REPO_DIR / "src-ui",
    timeout: int = 240,
) -> dict:
    """Run ``pnpm exec jest`` against ``pattern`` in src-ui and return parsed Jest JSON.

    Fallback for stories that drive the frontend directly instead of via the Jest driver.
    """
    cmd = [
        "pnpm",
        "exec",
        "jest",
        "--testPathPatterns",
        pattern,
        "--json",
        "--no-cache",
        "--forceExit",
    ]
    proc = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd),
        check=False,
    )
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
        "returncode": proc.returncode,
    }
