#!/usr/bin/env bash
# validation-setup.sh — runs once before the CC validation agent starts.
#
# This task makes the cloud-provider SDKs (daytona, e2b, modal, runloop,
# kubernetes, dockerfile-parse) OPTIONAL. The validation stories prove the
# solved repo behaves correctly with ONLY the lightweight core libraries
# present and NONE of those vendor SDKs. We therefore create a venv at
# /repo/harbor/.venv and install harbor editable with --no-deps plus the
# small always-required runtime libs and pytest.
#
# We deliberately do NOT run `uv sync` / install any cloud extra: pulling in
# the vendor SDKs would defeat the discrimination these stories depend on.
set -euo pipefail

REPO=/repo/harbor
VENV="$REPO/.venv"
cd "$REPO"

# Fresh, vendor-SDK-free venv (idempotent: recreate cleanly).
python3 -m venv "$VENV"
PYBIN="$VENV/bin/python"

"$PYBIN" -m pip install --no-cache-dir --upgrade pip
"$PYBIN" -m pip install --no-cache-dir --no-deps -e "$REPO"
"$PYBIN" -m pip install --no-cache-dir \
    "pydantic>=2.11.7" \
    "toml>=0.10.2" \
    "shortuuid>=1.0.13" \
    "tenacity>=9.1.2" \
    "pytest>=8.4.2" \
    "pytest-asyncio>=1.2.0"

# Fail fast if the core module chain cannot be imported at all. We import
# base (not factory) here on purpose: base must import on both the unsolved
# and solved repo, whereas factory importing cleanly without vendor SDKs is
# exactly the behavior the stories are meant to confirm on the solved repo.
"$PYBIN" -c "import harbor.environments.base as b; assert b.BaseEnvironment"

echo "validation-setup: harbor core installed without vendor SDKs"
