#!/usr/bin/env bash
# validation-setup.sh — runs once before the CC validation agent starts.
# Installs the harbor package (editable) plus its full dependency tree and
# the project's `dev` group (pytest, pytest-asyncio) into a uv-managed venv
# at /repo/harbor/.venv, which is what [settings.pytest].python points at.
# Mirrors the repo's own workflow (uv + uv.lock). Idempotent; safe to re-run.

set -euo pipefail

REPO=/repo/harbor
cd "$REPO"

export PATH="/root/.local/bin:$PATH"   # uv

# Build the venv from uv.lock. The image does not pre-sync, so the first run
# resolves + downloads (network is available); re-runs are fast no-ops.
# --frozen pins to uv.lock; the `dev` group (pytest, pytest-asyncio) installs
# by default. Fall back to a plain sync if the lock is momentarily out of date.
uv sync --frozen 2>&1 | tail -5 || uv sync 2>&1 | tail -5

# Expose harbor's venv interpreter so `python`/`pytest` resolve to the
# editable install with the full dependency tree.
if [ -d "$REPO/.venv" ]; then
    export VIRTUAL_ENV="$REPO/.venv"
    export PATH="$REPO/.venv/bin:$PATH"
fi

# Smoke-import the public surfaces the stories exercise, to fail fast if the
# environment is broken. (These resolve only after the agent's implementation
# exists; on an unimplemented tree this intentionally surfaces the gap.)
python - <<'PY'
import importlib
import harbor  # noqa: F401  (verifies installed metadata is present)
for mod in (
    "harbor.models.task.config",
    "harbor.models.task.paths",
    "harbor.models.trial.paths",
    "harbor.trial.trial",
    "harbor.agents.base",
    "harbor.agents.nop",
    "harbor.agents.oracle",
):
    importlib.import_module(mod)
import pytest  # noqa: F401
print("harbor + pytest import OK")
PY
