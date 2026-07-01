"""Test harness for posthog-feat-subscriptions-auto-disable validation.

Exposes pre-existing PostHog test infrastructure so each validation story can
focus on the behaviour under test rather than reinventing setup:

  - the Org -> Project -> Team + User factory
    (``setup_test_organization_team_and_user``)
  - an EE License + product-feature sync (subscriptions are an Enterprise
    feature; without a current license the endpoints 4xx before any feature
    logic runs)
  - a force-logged-in DRF ``APIClient``
  - ``Subscription`` / ``Integration`` / ``Insight`` / ``ExportedAsset`` ORM
    factories (all pre-existing models)
  - drivers for the pre-existing Temporal delivery interfaces:
      * ``run_process_workflow`` — runs the full ``ProcessSubscriptionWorkflow``
        end-to-end in an ephemeral time-skipping Temporal environment, with the
        ClickHouse / export / Slack-integration boundaries mocked. This is the
        highest-level delivery interface, so a story that asserts on the
        resulting subscription/delivery state is robust to *where* a solution
        places its auto-disable check.
      * ``run_deliver_activity`` — runs the single pre-existing
        ``deliver_subscription`` activity via ``ActivityEnvironment``.
      * ``run_fetch_due`` — runs the pre-existing
        ``fetch_due_subscriptions_activity``.
  - ``mock_temporal_sync_connect`` — patches the pre-existing
    ``ee.api.subscription.sync_connect`` so PATCH/POST requests don't talk to a
    real cluster and the story can count workflow triggers.
  - ``latest_delivery`` / ``failed_delivery_count`` — read back
    ``SubscriptionDelivery`` rows.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import uuid
import importlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from unittest import mock

# PostHog repo root on sys.path before any Django import so
# ``DJANGO_SETTINGS_MODULE=posthog.settings`` resolves under a bare ``python``
# invocation (pytest-django would normally handle this via rootdir/pythonpath).
_REPO_ROOT = "/repo/posthog"
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("TEST", "1")
os.environ.setdefault("DEBUG", "1")
os.environ.setdefault("DATABASE_URL", "postgres://posthog:posthog@localhost:5432/posthog")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-senior-swe-bench")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "posthog.settings")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("OBJECT_STORAGE_ENABLED", "False")
os.environ.setdefault("CLICKHOUSE_HOST", "localhost")
os.environ.setdefault("CLICKHOUSE_SECURE", "False")
os.environ.setdefault("CLICKHOUSE_VERIFY", "False")

import django  # noqa: E402

django.setup()

from django.utils import timezone  # noqa: E402

from posthog.test.base import setup_test_organization_team_and_user  # noqa: E402


# ---------------------------------------------------------------------------
# Org / Team / User / License
# ---------------------------------------------------------------------------


def _ensure_license() -> None:
    """Create an enterprise EE ``License`` row if none is active.

    Mirrors ``ee.api.test.base.LicensedTestMixin``: bypass the custom
    ``LicenseManager.create`` (which validates against the license server) via
    the base manager and set a far-future ``valid_until``. Subscriptions gate
    on ``AvailableFeature.SUBSCRIPTIONS`` which an enterprise license grants.
    """
    from ee.models.license import License, LicenseManager

    if License.objects.exists():
        return
    super(LicenseManager, License.objects).create(
        key="12345::67890",
        plan="enterprise",
        valid_until=dt.datetime(2038, 1, 19, 3, 14, 7, tzinfo=dt.timezone.utc),
    )


def get_team_and_user():
    """Create a fresh Org -> Project -> Team + User chain (unique UUIDs) and
    return ``(team, user)``, with an active enterprise license and the org's
    product features synced so the EE subscriptions endpoints are reachable.
    """
    _ensure_license()
    token = f"val-token-{uuid.uuid4().hex[:8]}"
    email = f"val-{uuid.uuid4().hex[:6]}@test.com"
    org, project, team, user, _ = setup_test_organization_team_and_user(
        organization_name=f"validation-org-{uuid.uuid4().hex[:6]}",
        team_api_token=token,
        user_email=email,
        user_password="testpass",
    )
    # Sync available features now that a license exists so the org actually has
    # the SUBSCRIPTIONS premium feature.
    org.update_available_product_features()
    org.save()
    return team, user


def get_authenticated_client(user):
    """Return a DRF ``APIClient`` force-logged-in as ``user``."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# Model factories (all pre-existing models)
# ---------------------------------------------------------------------------


def create_insight(team, short_id=None, name="Validation insight"):
    from posthog.models.insight import Insight

    return Insight.objects.create(
        team=team,
        short_id=short_id or uuid.uuid4().hex[:8],
        name=name,
    )


def create_slack_integration(team, config=None):
    """Create a pre-existing ``Integration`` row of kind ``slack``."""
    from posthog.models.integration import Integration

    return Integration.objects.create(team=team, kind="slack", config=config or {})


def create_subscription(
    team,
    user,
    target_type="email",
    target_value="recipient@test.com",
    integration_id=None,
    insight=None,
    frequency="daily",
    interval=1,
    start_date=None,
    **schedule,
):
    """Thin wrapper over the pre-existing ``Subscription.objects.create``.

    Creates the row at the model's own default lifecycle state (enabled). This
    helper passes ONLY pre-existing model fields — it never names the lifecycle
    field, so a solution that backs the API ``enabled`` field with a
    differently-named model attribute (e.g. ``is_active`` + ``source=``) is
    created correctly via its own default. To put a subscription into the
    paused/disabled state for a test, PATCH ``{"enabled": false}`` through the
    API (the contract-fixed field) rather than writing the model attribute
    here. ``**schedule`` forwards extra recurrence fields (``until_date``,
    ``count``, ``byweekday``, ``bysetpos``, ...) — all pre-existing.
    """
    from posthog.models.subscription import Subscription

    if insight is None:
        insight = create_insight(team)
    if start_date is None:
        start_date = timezone.now()

    return Subscription.objects.create(
        team=team,
        target_type=target_type,
        target_value=target_value,
        integration_id=integration_id,
        frequency=frequency,
        interval=interval,
        start_date=start_date,
        insight=insight,
        title="validation subscription",
        created_by=user,
        **schedule,
    )


def create_exported_asset(team, insight, content_location="s3://bucket/validation.png"):
    from posthog.models.exported_asset import ExportedAsset

    return ExportedAsset.objects.create(
        team=team,
        insight=insight,
        export_format="image/png",
        content_location=content_location,
    )


# ---------------------------------------------------------------------------
# Temporal mock for the API layer
# ---------------------------------------------------------------------------


@contextmanager
def mock_temporal_sync_connect():
    """Patch the pre-existing ``ee.api.subscription.sync_connect`` so create /
    update / test-delivery requests don't reach a real Temporal cluster.

    Yields the mock; ``mock.return_value.start_workflow`` is an ``AsyncMock``
    so a story can assert how many times the delivery workflow was triggered.
    """
    from unittest.mock import AsyncMock

    with mock.patch("ee.api.subscription.sync_connect") as patched:
        patched.return_value.start_workflow = AsyncMock()
        yield patched


# ---------------------------------------------------------------------------
# Subscription delivery readers
# ---------------------------------------------------------------------------


def latest_delivery(subscription):
    """Return the most recent ``SubscriptionDelivery`` row for the sub, or None."""
    from posthog.models.subscription import SubscriptionDelivery

    return (
        SubscriptionDelivery.objects.filter(subscription_id=subscription.id)
        .order_by("-created_at")
        .first()
    )


def failed_delivery_count(subscription):
    """Count ``SubscriptionDelivery`` rows in the FAILED status for the sub."""
    from posthog.models.subscription import SubscriptionDelivery

    return SubscriptionDelivery.objects.filter(
        subscription_id=subscription.id,
        status=SubscriptionDelivery.Status.FAILED,
    ).count()


def delivery_error_type(delivery):
    """Extract the per-recipient ``error.type`` token from a delivery row.

    Returns the first recipient result's ``error["type"]`` (the API contract
    tokens: ``missing_integration`` / ``unsupported_target`` / ``no_assets``),
    or ``None`` if absent.
    """
    if delivery is None:
        return None
    results = delivery.recipient_results or []
    for r in results:
        err = (r or {}).get("error") or {}
        if "type" in err:
            return err["type"]
    return None


# ---------------------------------------------------------------------------
# Temporal delivery drivers
# ---------------------------------------------------------------------------


def _subscription_activities():
    """The activity set to register on the worker: the canonical production
    ``ACTIVITIES`` list (which any valid solution extends with whatever
    pre-delivery validation activity it adds) plus the pre-existing
    ``export_asset_activity`` the export pipeline runs.
    """
    from posthog.temporal.subscriptions import ACTIVITIES
    from posthog.temporal.exports.activities import export_asset_activity

    acts = list(ACTIVITIES)
    if export_asset_activity not in acts:
        acts.append(export_asset_activity)
    return acts


def _snapshot_payload(insight):
    return {
        "id": insight.id,
        "short_id": str(insight.short_id),
        "name": insight.name or "",
        "dashboard_tile_id": None,
        "query_hash": "mock_cache_key",
        "cache_key": "mock_cache_key",
        "query_results": {"result": []},
    }


def run_process_workflow(subscription, *, slack_integration_present=False):
    """Drive the full ``ProcessSubscriptionWorkflow`` for ``subscription`` and
    return ``{"latest_status": str|None, "error_type": str|None,
    "failed_count": int, "completed_cleanly": bool}``.

    The ClickHouse / export / Slack boundaries are mocked exactly as PostHog's
    own subscription-workflow tests do (all pre-existing module paths):
      - ``build_insight_delivery_snapshot`` -> a static snapshot dict
      - ``exporter.export_asset_direct`` -> sets ``content_location`` so the
        export pipeline produces a deliverable asset
      - ``get_slack_integration_for_team`` -> ``None`` unless
        ``slack_integration_present`` is True

    This is the highest-level delivery interface, so reading the resulting
    state through the API afterward is robust to *where* a solution places its
    auto-disable check. The drivers do NOT read the lifecycle field off the
    model — the story reads the resulting ``enabled`` state through the API
    (the contract-fixed field), so the harness never names a model attribute.

    ``completed_cleanly`` reports whether the workflow returned WITHOUT
    surfacing an exception. A correct solution treats a permanently-broken
    target as a handled, terminal outcome (disable + return), so the workflow
    completes cleanly; a solution that instead raises the delivery failure out
    of the workflow (recording an SLO/delivery failure for a handled case)
    reports ``False``.
    """
    from django.conf import settings

    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from posthog.temporal.subscriptions.types import TrackedSubscriptionInputs
    from posthog.temporal.subscriptions.workflows import ProcessSubscriptionWorkflow
    from posthog.temporal.common.slo_interceptor import SloInterceptor

    insight = subscription.insight
    if insight is not None:
        # Ensure a deliverable asset exists for solutions that only disable from
        # inside deliver_subscription (i.e. the export pipeline must run first).
        create_exported_asset(subscription.team, insight)

    def fake_export(asset_obj, **kwargs):
        asset_obj.content_location = "s3://bucket/validation.png"
        asset_obj.save(update_fields=["content_location"])

    slack_value = object() if slack_integration_present else None

    raised = {"flag": False}

    async def _run():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue=settings.TEMPORAL_TASK_QUEUE,
                workflows=[ProcessSubscriptionWorkflow],
                activities=_subscription_activities(),
                interceptors=[SloInterceptor()],
                workflow_runner=UnsandboxedWorkflowRunner(),
                activity_executor=ThreadPoolExecutor(max_workers=10),
                debug_mode=True,
            ):
                try:
                    await env.client.execute_workflow(
                        ProcessSubscriptionWorkflow.run,
                        TrackedSubscriptionInputs(
                            subscription_id=subscription.id,
                            team_id=subscription.team_id,
                            distinct_id=str(subscription.created_by.distinct_id),
                        ),
                        id=str(uuid.uuid4()),
                        task_queue=settings.TEMPORAL_TASK_QUEUE,
                        execution_timeout=dt.timedelta(minutes=5),
                    )
                except Exception:
                    # A naive tree surfaces a delivery failure as a workflow
                    # exception instead of handling it as a terminal outcome.
                    # The persisted state is still asserted (read via the API);
                    # ``completed_cleanly`` records that this happened.
                    raised["flag"] = True

    # ``get_slack_integration_for_team`` is a delivery-path helper. A valid
    # auto-disable solution may detect a missing Slack integration by reading
    # ``subscription.integration_id`` directly (the contract-fixed signal the
    # disable logic keys off) and drop this helper entirely. Patch it ONLY when
    # the symbol still exists, so such a refactor isn't turned into an
    # AttributeError that errors every story for the wrong reason. No story
    # exercises a successful Slack send (every call uses
    # ``slack_integration_present=False``, so the helper would return ``None``
    # anyway), so skipping the patch when the symbol is absent changes no
    # observable behaviour — presence/absence is driven by the subscription's
    # real ``integration_id``, set by the story.
    sub_activities = importlib.import_module(
        "posthog.temporal.subscriptions.activities"
    )
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch(
                "posthog.temporal.subscriptions.activities.build_insight_delivery_snapshot",
                return_value=_snapshot_payload(insight) if insight is not None else {},
            )
        )
        if hasattr(sub_activities, "get_slack_integration_for_team"):
            stack.enter_context(
                mock.patch(
                    "posthog.temporal.subscriptions.activities.get_slack_integration_for_team",
                    return_value=slack_value,
                )
            )
        mock_exporter = stack.enter_context(
            mock.patch("posthog.temporal.exports.activities.exporter")
        )
        mock_exporter.export_asset_direct = fake_export
        asyncio.run(_run())

    delivery = latest_delivery(subscription)
    return {
        "latest_status": getattr(delivery, "status", None),
        "error_type": delivery_error_type(delivery),
        "failed_count": failed_delivery_count(subscription),
        "completed_cleanly": not raised["flag"],
    }


def run_deliver_activity(subscription, exported_asset_ids):
    """Run the single pre-existing ``deliver_subscription`` activity via
    ``ActivityEnvironment`` and return
    ``{"recipient_results": list, "error_type": str|None, "completed_cleanly": bool}``.

    ``exported_asset_ids`` lets a story exercise the transient no-assets path by
    passing ids that don't resolve to deliverable assets. As with
    ``run_process_workflow`` the resulting ``enabled`` state is read through the
    API by the story, not off the model here. ``completed_cleanly`` reports
    whether the activity returned without raising.
    """
    from temporalio.testing import ActivityEnvironment

    from posthog.temporal.subscriptions.activities import deliver_subscription
    from posthog.temporal.subscriptions.types import DeliverSubscriptionInputs

    inputs = DeliverSubscriptionInputs(
        subscription_id=subscription.id,
        exported_asset_ids=list(exported_asset_ids),
        total_insight_count=1,
    )

    async def _run():
        return await ActivityEnvironment().run(deliver_subscription, inputs)

    completed_cleanly = True
    result = None
    try:
        result = asyncio.run(_run())
    except Exception:
        completed_cleanly = False

    recipient_results = []
    error_type = None
    if result is not None:
        recipient_results = [
            r if isinstance(r, dict) else getattr(r, "__dict__", {})
            for r in (result.recipient_results or [])
        ]
        for r in recipient_results:
            err = (r or {}).get("error") or {}
            if "type" in err:
                error_type = err["type"]
                break
    return {
        "recipient_results": recipient_results,
        "error_type": error_type,
        "completed_cleanly": completed_cleanly,
    }


def run_fetch_due(buffer_minutes=15):
    """Run the pre-existing ``fetch_due_subscriptions_activity`` via
    ``ActivityEnvironment`` and return the set of subscription ids it yields.
    """
    from temporalio.testing import ActivityEnvironment

    from posthog.temporal.subscriptions.activities import fetch_due_subscriptions_activity
    from posthog.temporal.subscriptions.types import FetchDueSubscriptionsActivityInputs

    inputs = FetchDueSubscriptionsActivityInputs(buffer_minutes=buffer_minutes)

    async def _run():
        return await ActivityEnvironment().run(fetch_due_subscriptions_activity, inputs)

    result = asyncio.run(_run())
    ids = set()
    for sub in result:
        sid = getattr(sub, "subscription_id", None)
        if sid is None and isinstance(sub, dict):
            sid = sub.get("subscription_id") or sub.get("id")
        if sid is not None:
            ids.add(int(sid))
    return ids
