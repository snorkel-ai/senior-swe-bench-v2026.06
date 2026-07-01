"""Test harness for posthog-feat-prompt-versioning validation.

Exposes pre-existing PostHog test infrastructure (Org → Project → Team
factory, DRF APIClient with auth, feature-flag patch context manager) so
each story's procedure can stay focused on the behaviour under test
rather than reinventing setup.

Nothing here is implementation-specific. The helpers consume only:

- the pre-existing ``setup_test_organization_team_and_user`` factory
  from ``posthog.test.base`` (see ``docs/repo-notes/posthog.md``);
- the DRF ``APIClient.force_login`` pattern that PostHog's own API
  tests use (``posthog.test.base.APIBaseTest.setUp``);
- the pre-existing feature-gate decorator on the LLM-Prompt viewset,
  which checks ``posthoganalytics.feature_enabled`` for either
  ``prompt-management`` or ``llm-analytics-early-adopters``.

The harness deliberately does NOT import from any task-introduced
module (``posthog.api.services.llm_prompt``,
``posthog.api.llm_prompt_serializers``, etc.). All publish / archive /
versioning behaviour is observed only through the agent's HTTP
endpoints — the same way a real client would interact with the API.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid

# Django + Postgres env defaults. The validation-setup.sh writes the
# same values; setting them here too means a script can `import
# test_harness` first and have everything wired without depending on
# shell ordering.
os.environ.setdefault("TEST", "1")
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault(
    "DATABASE_URL", "postgres://posthog:posthog@localhost:5432/posthog"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-senior-swe-bench")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
# Disable object-storage so HyperCache's S3 fallback is bypassed in
# tests (HyperCache then runs Redis-only).
os.environ.setdefault("OBJECT_STORAGE_ENABLED", "False")

import django  # noqa: E402

django.setup()

from posthog.test.base import setup_test_organization_team_and_user  # noqa: E402


def get_team_and_user():
    """Create a fresh Org → Project → Team + User chain and return
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


def get_authenticated_client(user):
    """Return a DRF ``APIClient`` force-logged-in as ``user``.

    Mirrors the pattern PostHog's own API tests use (see
    ``posthog.test.base.APIBaseTest.setUp``).
    """
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_login(user)
    return client


@contextlib.contextmanager
def enable_llm_prompts_feature():
    """Patch ``posthoganalytics.feature_enabled`` so the
    ``LLMPromptFeatureFlagPermission`` permission class on the
    LLM-Prompt viewset always returns ``True``.

    The patch target (``posthog.api.llm_prompt.posthoganalytics.feature_enabled``)
    is the pre-existing import in the viewset module — the same one
    the repo's own tests for this surface use.

    Use as::

        with enable_llm_prompts_feature():
            response = client.post(...)
    """
    from unittest.mock import patch

    with patch(
        "posthog.api.llm_prompt.posthoganalytics.feature_enabled",
        return_value=True,
    ) as mocked:
        yield mocked


def parse_json_response(response):
    """Best-effort JSON decode of a DRF ``Response`` object.

    Returns ``None`` on failure (e.g. 204 No Content). Stories use this
    so they don't crash on unexpected status codes — they assert on the
    status separately.
    """
    try:
        body = response.content
        if not body:
            return None
        return json.loads(body)
    except Exception:
        return None
