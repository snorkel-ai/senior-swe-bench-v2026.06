"""Validation test harness: PostHog Org/Project/Team factory, schema ORM
helpers, and an authenticated DRF APIClient for the pytest stories.
"""

from __future__ import annotations

import os
import uuid

# Django + Postgres env defaults, so importing this module wires the env
# regardless of shell ordering.
os.environ.setdefault("TEST", "1")
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault(
    "DATABASE_URL", "postgres://posthog:posthog@localhost:5432/posthog"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-senior-swe-bench")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("OBJECT_STORAGE_ENABLED", "False")

import django  # noqa: E402

django.setup()

from posthog.test.base import setup_test_organization_team_and_user  # noqa: E402


def get_team_and_user():
    """Create a fresh Org → Project → Team + User chain; return ``(team, user)``."""
    token = f"val-token-{uuid.uuid4().hex[:8]}"
    email = f"val-{uuid.uuid4().hex[:6]}@test.com"
    org, project, team, user, _ = setup_test_organization_team_and_user(
        organization_name=f"validation-org-{uuid.uuid4().hex[:6]}",
        team_api_token=token,
        user_email=email,
        user_password="testpass",
    )
    return team, user


def get_authenticated_client(user):
    """Return a DRF ``APIClient`` force-logged-in as ``user``."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_login(user)
    return client


def create_event_definition(team, name=None, enforcement_mode="allow"):
    """Create an ``EventDefinition``. ``enforcement_mode`` is ``"allow"`` or ``"reject"``."""
    from posthog.models import EventDefinition

    if name is None:
        name = f"event-{uuid.uuid4().hex[:6]}"
    return EventDefinition.objects.create(
        team=team,
        project=team.project,
        name=name,
        enforcement_mode=enforcement_mode,
    )


def create_property_group(team, name=None):
    """Create a ``SchemaPropertyGroup`` on ``team``."""
    from posthog.models import SchemaPropertyGroup

    if name is None:
        name = f"group-{uuid.uuid4().hex[:6]}"
    return SchemaPropertyGroup.objects.create(
        team=team,
        project=team.project,
        name=name,
    )


def add_property(group, name, property_type="String", required=True):
    """Attach a ``SchemaPropertyGroupProperty`` to ``group``.

    ``property_type`` is one of: ``String``, ``Numeric``, ``Boolean``,
    ``DateTime``, ``Object``.
    """
    from posthog.models import SchemaPropertyGroupProperty

    return SchemaPropertyGroupProperty.objects.create(
        property_group=group,
        name=name,
        property_type=property_type,
        is_required=required,
    )


def link_schema(event_definition, property_group):
    """Create the ``EventSchema`` row joining ``event_definition`` and
    ``property_group``."""
    from posthog.models import EventSchema

    return EventSchema.objects.create(
        event_definition=event_definition,
        property_group=property_group,
    )
