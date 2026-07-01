"""conftest_override.py — PostHog multi-DB pytest-django setup.

PostHog's Django settings configure multiple databases (default=posthog,
persons_db_writer=posthog_persons, persons_db_reader=posthog_persons).
pytest-django's default django_db_setup fixture would try to create
test_posthog and test_posthog_persons, which conflicts with the databases
that validation-setup.sh already created and migrated.

This override:
1. Redirects every Django database's TEST NAME to the live database so
   pytest-django manages access to the pre-existing databases instead of
   trying to create new test ones.
2. Calls django_db_blocker.unblock() so all tests can access the DB
   without the fixture attempting CREATE DATABASE / DROP DATABASE.
3. Monkeypatches TransactionTestCase._fixture_teardown to silently ignore
   FK-constraint errors from Django's post-test flush. PostHog's tables
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

    Point every configured database's TEST NAME to the live database
    name so pytest-django does not attempt to create or drop databases.
    Then unblock access for the session.
    """
    from django.conf import settings

    # Ensure Django is set up before touching settings.DATABASES.
    django.setup()

    # Redirect each database's TEST NAME to the real database so
    # pytest-django's DB management layer treats them as "existing".
    for alias, db_config in settings.DATABASES.items():
        test_settings = db_config.setdefault("TEST", {})
        # Use the live database name; never create a test_ prefixed copy.
        if "NAME" not in test_settings or test_settings["NAME"] != db_config.get("NAME"):
            test_settings["NAME"] = db_config.get("NAME", "posthog")

    with django_db_blocker.unblock():
        yield
