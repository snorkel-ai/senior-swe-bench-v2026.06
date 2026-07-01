"""Validation conftest override for prefect-feat-dbt-per-node-hardening.

The pytest driver auto-loads this file as a pytest plugin, so everything
defined here is visible to the test files the driver runs.

Exposes one autouse, session-scoped fixture: `_prefect_db`. Every story that
exercises `PrefectDbtOrchestrator.run_build()` in PER_NODE mode does so inside
a `@flow`, which requires an active Prefect server context, so the fixture
opens a single `prefect_test_harness` for the whole pytest session — all
stories share one ephemeral SQLite-backed Prefect API server (and the
flow-run / task-run reads the graph-edge story performs hit that same server).
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _prefect_db():
    """Open a session-scoped ephemeral Prefect server for every story."""
    from prefect.settings import (
        PREFECT_API_SERVICES_TRIGGERS_ENABLED,
        temporary_settings,
    )
    from prefect.testing.utilities import prefect_test_harness

    with temporary_settings({PREFECT_API_SERVICES_TRIGGERS_ENABLED: False}):
        with prefect_test_harness(server_startup_timeout=60):
            yield
