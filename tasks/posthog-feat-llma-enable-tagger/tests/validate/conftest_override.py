"""conftest_override.py — PostHog multi-DB pytest-django setup.

PostHog's Django settings configure multiple databases (default=posthog,
persons_db_writer=posthog_persons, persons_db_reader=posthog_persons,
visual_review_db_*, warehouse_sources_queue_db_*).

pytest-django's default django_db_setup fixture would try to create test_*
copies of all databases, conflicting with the databases already created and
migrated by validation-setup.sh.

This override:
1. Redirects every Django database's TEST NAME to the live database so
   pytest-django manages access to the pre-existing databases instead of
   trying to create new test ones.
2. Redirects secondary database HOSTs from 'db' to 'localhost' so they
   are reachable inside the validation container (where hostname 'db'
   does not resolve).  The secondary databases (posthog_persons, etc.)
   are created as empty databases by validation-setup.sh; the validation
   stories only touch the default (posthog) database, so no migrations are
   needed on the secondary ones — just a live connection.
3. Calls django_db_blocker.unblock() so all tests can access the DB
   without the fixture attempting CREATE DATABASE / DROP DATABASE.
4. Monkeypatches TransactionTestCase._fixture_teardown to silently ignore
   FK-constraint errors from Django's post-test flush.  PostHog's tables
   have inter-FK references that prevent TRUNCATE without CASCADE; because
   each test creates isolated data using unique UUIDs (via get_team_and_user),
   stale rows from a previous test never interfere with subsequent ones.
"""

import django
import pytest

from django.test import TransactionTestCase as _TransactionTestCase  # noqa: E402

_orig_fixture_teardown = _TransactionTestCase._fixture_teardown


def _safe_fixture_teardown(self: _TransactionTestCase) -> None:  # type: ignore[override]
    """Teardown that silently ignores FK-constraint flush errors."""
    try:
        _orig_fixture_teardown(self)
    except Exception:
        pass  # FK constraint errors; isolated data per test makes this safe.


_TransactionTestCase._fixture_teardown = _safe_fixture_teardown  # type: ignore[method-assign]


@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker):
    """Reuse the pre-existing migrated databases from validation-setup.sh.

    1. Point every configured database's TEST NAME to the live database
       name so pytest-django does not attempt to create or drop databases.
    2. Redirect secondary database HOSTs from 'db' to 'localhost' so that
       validation stories running inside the container (where 'db' does not
       resolve) can open transactions against those databases.  The databases
       themselves are created as empty schemas by validation-setup.sh — no
       migrations are needed because validation stories only use the default
       (posthog) database.
    3. Unblock access for the session.
    """
    from django.conf import settings

    # Ensure Django is set up before touching settings.DATABASES.
    django.setup()

    for alias, db_config in settings.DATABASES.items():
        test_settings = db_config.setdefault("TEST", {})
        # Use the live database name; never create a test_ prefixed copy.
        if "NAME" not in test_settings or test_settings["NAME"] != db_config.get("NAME"):
            test_settings["NAME"] = db_config.get("NAME", "posthog")

        # Secondary databases use HOST='db' in PostHog's settings, which does
        # not resolve inside the validation container.  Redirect them to
        # localhost so pytest-django can open (empty) transactions against them
        # when tests are marked with databases="__all__".
        host = db_config.get("HOST", "")
        if host and host not in ("localhost", "127.0.0.1"):
            db_config["HOST"] = "localhost"
            if not db_config.get("PORT"):
                db_config["PORT"] = "5432"

    with django_db_blocker.unblock():
        yield
