#!/usr/bin/env bash
# test-setup.sh — sourced (not executed) before the verifier runs.
#
# This task is about making the third-party sandbox SDKs (daytona, e2b,
# modal, runloop, kubernetes/dockerfile-parse) OPTIONAL. The verifier proves
# that by exercising harbor with ONLY the lightweight core dependencies
# installed and NONE of the heavy vendor SDKs present.
#
# We install harbor editable with --no-deps so the package metadata exists
# (harbor/__init__.py calls importlib.metadata.version("harbor")) without
# dragging in any optional extras, then add only the small always-required
# runtime libs that the core import chain needs:
#   - pydantic, toml, shortuuid : models / config / utils
#   - tenacity                  : retry decorators used directly by several
#                                 environment modules at import time
#   - pytest, pytest-asyncio    : test runner
#
# Deliberately NOT installed: daytona, e2b, e2b-code-interpreter, modal,
# runloop-api-client, kubernetes, dockerfile-parse. Their ABSENCE is the
# whole point — a correct solution must keep `import harbor.environments.*`
# working without them.
set -euo pipefail

python3 -m pip install --no-cache-dir -e /repo/harbor --no-deps
python3 -m pip install --no-cache-dir \
    "pydantic>=2.11.7" \
    "toml>=0.10.2" \
    "shortuuid>=1.0.13" \
    "tenacity>=9.1.2" \
    "pytest>=8.4.2" \
    "pytest-asyncio>=1.2.0"

# Fail fast if the core module chain cannot be imported at all. We import
# base (not factory) here on purpose: base must import on both the unsolved
# and solved repo, whereas factory importing cleanly without vendor SDKs is
# exactly the behavior the verifier is meant to discriminate.
python3 -c "import harbor.environments.base as b; assert b.BaseEnvironment"
