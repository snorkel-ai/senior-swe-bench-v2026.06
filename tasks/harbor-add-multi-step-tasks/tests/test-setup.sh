#!/usr/bin/env bash
# Container setup for verifiers. This file is `source`d (not executed as a
# subprocess) so the exported PATH/VIRTUAL_ENV persist into the verifier
# process — the verifier runs under harbor's own uv-managed virtualenv, which
# has the harbor package (editable) plus its full dependency tree plus pytest
# (the project's `dev` dependency-group) already installed.
set -u

REPO=/repo/harbor
cd "$REPO" || { echo "test-setup: $REPO missing" >&2; return 1 2>/dev/null || exit 1; }

export PATH="/root/.local/bin:$PATH"   # uv

# Build/verify the venv. The image pre-runs `uv sync`, so on a normal trial
# this is a fast no-op (resolution against the existing .venv, no downloads).
# --frozen pins to uv.lock; the `dev` group (pytest, pytest-asyncio) installs
# by default.
uv sync --frozen 2>&1 | tail -3 || uv sync 2>&1 | tail -3

# Make harbor's venv interpreter the default `python`/`python3`/`pytest` the
# verifier will invoke, so `import harbor...` resolves to the editable repo
# install with all its deps available.
if [ -d "$REPO/.venv" ]; then
    export VIRTUAL_ENV="$REPO/.venv"
    export PATH="$REPO/.venv/bin:$PATH"
fi

# Sanity: the modules under test must import cleanly before any verifier runs.
python -c "import harbor.trial.trial; import harbor.models.task.config; import harbor.models.trial.result" \
    && echo "test-setup: harbor importable" \
    || echo "test-setup: WARNING harbor import failed" >&2
