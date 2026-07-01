"""Behavioral verifier — saved view visibility fields removed from SavedView.

Tests through Django's stable ORM ``Model._meta`` API: removing the
``show_on_dashboard`` and ``show_in_sidebar`` columns (visibility moves to
each user's UiSettings record) makes their lookup raise ``FieldDoesNotExist``.
Invoked by ``verify.sh`` via ``uv run pytest`` so paperless's deps resolve
through the uv-managed venv.
"""

from __future__ import annotations

import os
import sys

# PAPERLESS_* env vars must be set before Django import; setdefault is defensive.
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

# paperless-ngx packages live under src/.
if "/repo/paperless-ngx/src" not in sys.path:
    sys.path.insert(0, "/repo/paperless-ngx/src")

import django  # noqa: E402

django.setup()

import pytest  # noqa: E402
from django.core.exceptions import FieldDoesNotExist  # noqa: E402

from documents.models import SavedView  # noqa: E402


def test_data_migration_exists() -> None:
    """A documents migration exists beyond the pre-feature leaf.

    A migration is required to move visibility data from SavedView fields into
    UiSettings JSON; at least one must come after the pre-feature leaf.
    """
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    all_doc = sorted(name for app, name in loader.graph.nodes if app == "documents")

    PRE_FIX_LEAF = "0014_alter_paperlesstask_task_name"
    assert PRE_FIX_LEAF in all_doc, (
        f"Pre-fix leaf migration '{PRE_FIX_LEAF}' not found in documents "
        f"migrations. Available: {all_doc[-5:]}"
    )
    post_fix = [m for m in all_doc if m > PRE_FIX_LEAF]
    assert post_fix, (
        f"No migration exists after '{PRE_FIX_LEAF}'. A data migration is "
        f"required to move visibility preferences into UiSettings."
    )


def test_show_on_dashboard_field_removed() -> None:
    """The legacy ``show_on_dashboard`` column must be removed from SavedView."""
    with pytest.raises(FieldDoesNotExist):
        SavedView._meta.get_field("show_on_dashboard")


def test_show_in_sidebar_field_removed() -> None:
    """The legacy ``show_in_sidebar`` column must be removed from SavedView."""
    with pytest.raises(FieldDoesNotExist):
        SavedView._meta.get_field("show_in_sidebar")
