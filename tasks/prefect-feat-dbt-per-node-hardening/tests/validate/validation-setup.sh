#!/usr/bin/env bash
# Installs dbt-duckdb + duckdb because one story (clean preview stdout) is only
# observable with a real dbt project parsed and listed — the bundled DuckDB
# project (tests/dbt_test_project) needs the duckdb adapter.

set -euo pipefail

REPO=/repo/prefect/src/integrations/prefect-dbt
cd "$REPO"

if [ ! -d .venv ]; then
    uv venv --python 3.11 .venv
fi
. .venv/bin/activate

uv pip install -e . --quiet
uv pip install pytest pytest-asyncio --quiet
uv pip install dbt-duckdb duckdb --quiet

# Pin FastAPI to the version the repo's uv.lock locks at the base commit
# (0.135.3). The dependency range is wide (`fastapi>=0.111.0,<1.0.0`), so an
# unpinned resolve installs the latest in-range FastAPI — and versions newer
# than 0.135.3 break Prefect's custom `PrefectRouter` in the ephemeral test
# server (`AttributeError: 'PrefectRouter' object has no attribute 'routes'`
# from fastapi's `effective_candidates`/`_get_routes_version`). Pinning back to
# the locked version restores the server every story's `@flow` runs against.
uv pip install "fastapi==0.135.3" --quiet

# Smoke-import to fail fast if the env is broken.
python - <<'PY'
import prefect
import prefect_dbt
import prefect_dbt.core._orchestrator
import prefect_dbt.core._manifest
import prefect_dbt.core._freshness
from prefect.task_runners import ThreadPoolTaskRunner
from prefect.testing.utilities import prefect_test_harness
from prefect import flow
print("prefect-dbt validation env OK")
PY
