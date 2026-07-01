"""Test harness for posthog-feat-llma-enable-tagger validation.

Exposes pre-existing PostHog test infrastructure (Org -> Project -> Team
factory, the Tagger ORM model, DRF APIClient with auth).

Nothing here is implementation-specific: the helpers consume only the
pre-existing ``Tagger`` model and the pre-existing
``setup_test_organization_team_and_user`` factory from
``posthog.test.base``. No serializer classes, viewset internals, or
reference-implementation-invented symbols are referenced anywhere — the stories
drive the public REST endpoint ``/api/environments/{team_id}/taggers/`` only.
"""

from __future__ import annotations

import os
import uuid

# Django + Postgres env defaults. validation-setup.sh writes the same
# values; setting them here too means a script can ``import test_harness``
# first and have everything wired without depending on shell ordering.
os.environ.setdefault("TEST", "1")
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault(
    "DATABASE_URL", "postgres://posthog:posthog@localhost:5432/posthog"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-senior-swe-bench")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import django  # noqa: E402

django.setup()

from posthog.test.base import setup_test_organization_team_and_user  # noqa: E402


def get_team_and_user():
    """Create a fresh Org -> Project -> Team + User chain and return
    ``(team, user)``.

    The pre-existing PostHog factory handles the multi-table FK
    constraints (``project_id_is_not_null`` etc.) and creates the
    required OrganizationMembership.
    """
    token = f"val-token-{uuid.uuid4().hex[:8]}"
    email = f"val-{uuid.uuid4().hex[:6]}@test.com"
    org, project, team, user, _ = setup_test_organization_team_and_user(
        organization_name=f"validation-org-{uuid.uuid4().hex[:6]}",
        team_api_token=token,
        user_email=email,
        user_password="testpass",
    )
    return team, user


def valid_tagger_config(**overrides):
    """Return a valid LLM tagger_config payload.

    Mirrors the ``_make_tagger_config`` helper used by the repo's own
    taggers API tests: an LLM tagger requires a ``prompt`` and at least
    one tag, which the model validates in ``Tagger.save()``.
    """
    defaults = {
        "prompt": "Which product features were discussed?",
        "tags": [
            {"name": "billing", "description": "Billing related"},
            {"name": "analytics", "description": "Analytics related"},
        ],
        "min_tags": 0,
        "max_tags": 2,
    }
    return {**defaults, **overrides}


def create_tagger(team, user, name=None, **overrides):
    """Create a persisted ``Tagger`` row via the ORM (not the API).

    Used by the update/edit stories that need a pre-existing tagger to
    PATCH. Uses only the pre-existing ``Tagger`` model.
    """
    from products.llm_analytics.backend.models.taggers import Tagger

    if name is None:
        name = f"tagger-{uuid.uuid4().hex[:6]}"
    fields = {
        "name": name,
        "tagger_config": valid_tagger_config(),
        "team": team,
        "created_by": user,
    }
    fields.update(overrides)
    return Tagger.objects.create(**fields)


def create_provider_key(team, name="Main key", provider="openai"):
    """Create a persisted ``LLMProviderKey`` row via the ORM.

    Used by the valid-create story to verify the server-derived
    ``provider_key_name`` (the key's name) survives read re-serialization in
    the create response. Uses only the pre-existing ``LLMProviderKey`` model;
    ``encrypted_config`` defaults to an empty dict so no secret is needed.
    """
    from products.llm_analytics.backend.models.provider_keys import LLMProviderKey

    return LLMProviderKey.objects.create(team=team, provider=provider, name=name)


def get_authenticated_client(user):
    """Return a DRF ``APIClient`` force-logged-in as ``user``.

    Mirrors the pattern PostHog's own API tests use (see
    ``posthog.test.base.APIBaseTest.setUp``).
    """
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_login(user)
    return client
