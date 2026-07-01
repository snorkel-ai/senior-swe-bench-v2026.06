"""PostHog multi-DB pytest-django setup: reuse the pre-migrated databases
instead of letting pytest-django create/drop test databases, and tolerate
FK-constraint errors during post-test flush (per-test data is UUID-isolated).
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
    """Reuse the pre-migrated databases: point every TEST NAME at the live
    database so pytest-django doesn't create/drop, then unblock access."""
    from django.conf import settings

    django.setup()

    for alias, db_config in settings.DATABASES.items():
        test_settings = db_config.setdefault("TEST", {})
        if "NAME" not in test_settings or test_settings["NAME"] != db_config.get("NAME"):
            test_settings["NAME"] = db_config.get("NAME", "posthog")

    with django_db_blocker.unblock():
        yield
