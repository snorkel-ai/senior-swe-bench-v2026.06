"""Test harness stub for turborepo-feat-add-circular-package.

This task uses the subprocess driver: each story shells out to the prebuilt
`turbo` CLI binary and prints a JSON result, so there is no shared Python state.
This file exists only because the build validator requires test_harness.py
alongside validation_spec.toml; stories may optionally import the shared env
vars / fixture-path helpers defined here.
"""

from __future__ import annotations

import os

# The prebuilt turbo binary that validation-setup.sh stages. Shared so
# stories don't have to spell the path out individually.
TURBO_BIN: str = "/repo/turborepo/target/debug/turbo"

# Root of staged fixture monorepos.
FIXTURES_ROOT: str = "/tmp/cycle_fixtures"

# Standard env vars that suppress turbo's telemetry prompts and update
# notices. Mirroring the upstream integration-test harness keeps story
# output free of unrelated noise.
TURBO_ENV: dict[str, str] = {
    "TURBO_TELEMETRY_MESSAGE_DISABLED": "1",
    "TURBO_GLOBAL_WARNING_DISABLED": "1",
    "TURBO_PRINT_VERSION_DISABLED": "1",
    "DO_NOT_TRACK": "1",
    "NPM_CONFIG_UPDATE_NOTIFIER": "false",
    "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
}


def fixture_dir(name: str) -> str:
    """Return the absolute path to a staged fixture monorepo.

    Available names: ``three_cycle``, ``two_cycle``, ``multi_cycle``,
    ``acyclic``. validation-setup.sh creates these under
    ``/tmp/cycle_fixtures/`` before stories run.
    """
    return os.path.join(FIXTURES_ROOT, name)


def merged_env() -> dict[str, str]:
    """Return ``os.environ`` merged with the turbo telemetry-suppression vars."""
    return {**os.environ, **TURBO_ENV}
