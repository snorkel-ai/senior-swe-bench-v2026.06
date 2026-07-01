#!/usr/bin/env bash
# validation-setup.sh — runs once before the CC validation agent starts.
# Ensures harbor's uv-managed venv exists and the package is importable.
# Mirrors tests/test-setup.sh: same repo, same package manager, same
# editable install. Safe to re-run.

set -euo pipefail

REPO=/repo/harbor
cd "$REPO"

export PATH="/root/.local/bin:$PATH"   # uv

# The image pre-runs `uv sync`, so this is normally a fast no-op. --frozen
# pins to uv.lock; the `dev` group (pytest, pytest-asyncio) installs by
# default, giving the validation scripts a pytest to run under.
uv sync --frozen 2>&1 | tail -3 || uv sync 2>&1 | tail -3

# Expose harbor's venv interpreter on PATH so `python`/`pytest` resolve to
# the editable install with the full dependency tree.
if [ -d "$REPO/.venv" ]; then
    export VIRTUAL_ENV="$REPO/.venv"
    export PATH="$REPO/.venv/bin:$PATH"
fi

# Smoke-import to fail fast if the env is broken.
python - <<'PY'
import harbor.trial.trial  # noqa: F401
import harbor.models.task.config  # noqa: F401
import harbor.models.trial.result  # noqa: F401
import harbor.models.trial.config  # noqa: F401
import harbor.environments.base  # noqa: F401
print("harbor import OK")
PY
