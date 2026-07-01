"""Test harness for posthog-feat-approval-gating validation.

Exposes pre-existing PostHog test infrastructure (Org → Project → Team
factory, FeatureFlag/ApprovalPolicy ORM, DRF APIClient with auth).

Nothing here is implementation-specific: the helpers consume only the
pre-existing models and the pre-existing
``setup_test_organization_team_and_user`` factory from
``posthog.test.base``. The conditions JSON shape is left to the
caller — every helper that takes ``conditions=`` passes the dict
through to ``ApprovalPolicy.objects.create(...)`` unmodified, so any
implementation's schema works.
"""

from __future__ import annotations

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


def create_feature_flag(team, key=None, rollout_percentage=None, filters=None, active=True):
    """Create a ``FeatureFlag`` on ``team``.

    Pass either ``rollout_percentage`` (which builds a single
    ``groups[]`` entry) or a fully-formed ``filters`` dict. If both
    are supplied, ``filters`` wins.
    """
    from posthog.models import FeatureFlag

    if filters is None:
        if rollout_percentage is None:
            rollout_percentage = 30
        filters = {"groups": [{"properties": [], "rollout_percentage": rollout_percentage}]}
    if key is None:
        key = f"flag-{uuid.uuid4().hex[:6]}"

    return FeatureFlag.objects.create(
        team=team,
        key=key,
        name=key,
        active=active,
        filters=filters,
        created_by=None,
    )


def create_approval_policy(
    team,
    user,
    action_key,
    conditions=None,
    enabled=True,
    team_scope=True,
    allow_self_approve=True,
):
    """Create an ``ApprovalPolicy`` whose conditions JSON is whatever
    the validation script supplies. The action_key is required — CC must
    discover the correct key from the agent's implementation.

    ``team_scope=True`` (default): policy is scoped to ``team`` (the
    canonical case). ``team_scope=False`` creates an org-level policy
    (``team=None``); use this in conjunction with a team-scoped policy
    to exercise the multi-policy conflict path, since the
    ``(organization, team, action_key)`` UNIQUE constraint forbids two
    team-level policies on the same action.

    ``approver_config`` is set with ``quorum=1`` and the supplied user
    in ``users`` so the policy is well-formed for any quorum-counting
    engine.
    """
    from posthog.approvals.models import ApprovalPolicy

    return ApprovalPolicy.objects.create(
        organization=team.organization,
        team=team if team_scope else None,
        action_key=action_key,
        conditions=conditions if conditions is not None else {},
        approver_config={"users": [user.id], "roles": [], "quorum": 1},
        allow_self_approve=allow_self_approve,
        bypass_roles=[],
        enabled=enabled,
        created_by=user,
    )


def get_authenticated_client(user):
    """Return a DRF ``APIClient`` force-logged-in as ``user``.

    Mirrors the pattern PostHog's own API tests use (see
    ``posthog.test.base.APIBaseTest.setUp``).
    """
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_login(user)
    return client
