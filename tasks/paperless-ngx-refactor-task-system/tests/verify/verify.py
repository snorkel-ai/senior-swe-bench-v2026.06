"""Behavioral verifier — the third-party ``django_celery_results`` result
backend is fully removed from the runtime configuration.

The redesign drops ``django_celery_results`` entirely: it must no longer be
an installed Django app, and the Celery result backend must no longer point
at the ``django-db`` store that app provided. Reads only Django's own
``settings`` object, a pre-existing public interface.
"""

from __future__ import annotations

import os
import sys

# Repo-notes Gotcha: PAPERLESS_* env vars MUST be set before Django import.
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

from django.conf import settings  # noqa: E402


def test_django_celery_results_removed_from_installed_apps() -> None:
    """``django_celery_results`` is no longer an installed Django app."""
    assert "django_celery_results" not in settings.INSTALLED_APPS, (
        "django_celery_results must be removed from INSTALLED_APPS; "
        f"found in: {list(settings.INSTALLED_APPS)}"
    )


def test_celery_result_backend_setting_dropped() -> None:
    """The Celery result backend no longer points at the django-db store.

    The removed app provided the ``django-db`` result backend; dropping the
    app means the backend setting must no longer select it. Either the
    setting is gone entirely (``getattr`` → None) or it has been repointed,
    both of which satisfy this check.
    """
    backend = getattr(settings, "CELERY_RESULT_BACKEND", None)
    assert backend != "django-db", (
        "CELERY_RESULT_BACKEND must no longer be 'django-db' "
        "(the django_celery_results store); got: "
        f"{backend!r}"
    )
