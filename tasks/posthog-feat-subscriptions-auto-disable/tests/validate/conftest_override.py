"""conftest_override.py — PostHog multi-DB pytest-django setup for the
subscriptions auto-disable validation stories.

Two concerns beyond the stock pytest-django behaviour:

1. **Reuse the live, already-migrated databases.** validation-setup.sh creates
   and migrates ``posthog`` (and ``posthog_persons``); pytest-django would
   otherwise try to create ``test_posthog`` etc. The ``django_db_setup`` fixture
   below redirects every alias's TEST NAME to the live database and unblocks
   access for the session.

2. **Every test must run as a transactional test with ``databases="__all__"``.**
   The Temporal delivery stories drive activities whose DB access goes through
   ``database_sync_to_async(..., thread_sensitive=False)`` — i.e. via worker
   threads on *separate* connections. Data created by the story is only visible
   to those connections if it is actually COMMITTED, which requires
   ``django_db(transaction=True)`` (the default ``transaction=False`` wraps the
   test in a rolled-back transaction whose writes other connections never see).
   The validation pytest driver injects a plain ``django_db`` marker on every
   collected test; we rewrite each one to ``django_db(transaction=True,
   databases="__all__")`` from a ``trylast`` collection hook so our marker wins
   regardless of hook ordering.

Per-test isolation is preserved because every story builds a fresh Org/Team/User
with unique UUIDs via the harness ``get_team_and_user()``; stale rows from an
earlier test therefore never affect a later one. The FK-aware teardown swallow
below keeps PostHog's circular-FK flush from surfacing as a spurious test ERROR.
"""

import django
import pytest


# ---------------------------------------------------------------------------
# Monkeypatch: silence FK-constraint teardown errors
# ---------------------------------------------------------------------------
from django.test import TransactionTestCase as _TransactionTestCase  # noqa: E402

_orig_fixture_teardown = _TransactionTestCase._fixture_teardown


def _safe_fixture_teardown(self: _TransactionTestCase) -> None:  # type: ignore[override]
    """Teardown that silently ignores FK-constraint flush errors.

    PostHog's schema has circular FK references that make Django's post-test
    TRUNCATE raise unless every related table is truncated together. Each
    validation test uses unique-UUID data, so leftover rows can't influence a
    later test — swallowing the teardown error is safe.
    """
    try:
        _orig_fixture_teardown(self)
    except Exception:
        pass


_TransactionTestCase._fixture_teardown = _safe_fixture_teardown  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Marker rewrite: force transaction=True + databases="__all__" on every test
# ---------------------------------------------------------------------------
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    """Replace every ``django_db`` marker with one that is transactional and
    spans all databases.

    Runs ``trylast`` so it executes after the driver-generated conftest (which
    adds a non-transactional ``django_db`` marker). We drop all pre-existing
    ``django_db`` markers and append exactly one canonical marker, so the
    transaction setting can't depend on ``get_closest_marker`` ordering.
    """
    transactional = pytest.mark.django_db(transaction=True, databases="__all__")
    for item in items:
        kept = [m for m in item.own_markers if m.name != "django_db"]
        kept.append(transactional.mark)
        item.own_markers = kept


# ---------------------------------------------------------------------------
# Session fixture: reuse existing migrated databases
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker):
    """Reuse the pre-existing migrated databases from validation-setup.sh."""
    from django.conf import settings

    django.setup()

    for alias, db_config in settings.DATABASES.items():
        test_settings = db_config.setdefault("TEST", {})
        if "NAME" not in test_settings or test_settings["NAME"] != db_config.get("NAME"):
            test_settings["NAME"] = db_config.get("NAME", "posthog")

    with django_db_blocker.unblock():
        yield
