"""Minimal nop-gate verifier for optional sandbox deps.

The behavioral contract is carried by the validation stories
(validate/validation_spec.toml), the primary reward signal. This file is a
small, deterministic nop-gate:

  * ``test_pyproject_providers_optional`` — provider SDKs are no longer
    mandatory core dependencies and are reachable as optional extras.
  * ``test_local_backends_not_gated`` — guards against an over-gating fix that
    hides the local/first-class backends behind an optional extra.

Imports happen inside the test bodies so the file always collects cleanly even
with no vendor SDKs present.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path("/repo/harbor")

# Vendor sandbox-provider SDK *distribution* names that must no longer be
# mandatory core dependencies.
_PROVIDER_DISTRIBUTIONS = [
    "daytona",
    "e2b",
    "modal",
    "runloop-api-client",
    "kubernetes",
]


def _make_trial_paths(tmp_path: Path):
    """Build a TrialPaths rooted under a real temp dir."""
    from harbor.models.trial.paths import TrialPaths

    trial_root = tmp_path / "trial"
    trial_root.mkdir(parents=True, exist_ok=True)
    return TrialPaths(trial_root)


# ── packaging metadata: providers optional, not mandatory ─────────────────


def _flatten_extra(opt_deps: dict[str, list[str]], extra: str, seen=None) -> list[str]:
    """Recursively expand an optional-dependency extra, following
    ``harbor[...]`` self-references into aggregate extras."""
    import re

    if seen is None:
        seen = set()
    if extra in seen:
        return []
    seen.add(extra)

    result: list[str] = []
    for spec in opt_deps.get(extra, []):
        m = re.match(r"^harbor\[([^\]]+)\]", spec.strip())
        if m:
            for sub in m.group(1).split(","):
                result.extend(_flatten_extra(opt_deps, sub.strip(), seen))
        else:
            result.append(spec)
    return result


def _dist_name(requirement: str) -> str:
    """Extract the lowercased distribution name from a PEP 508 requirement."""
    import re

    name = re.split(r"[<>=!~;\[\s]", requirement.strip(), maxsplit=1)[0]
    return name.strip().lower()


def test_pyproject_providers_optional():
    """Provider SDKs must be absent from mandatory [project.dependencies] and
    reachable as optional extras (directly or transitively via an aggregate
    extra). Agnostic to the exact extra names and bundling.
    """
    pyproject = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())

    project = data["project"]
    core_deps = {_dist_name(d) for d in project.get("dependencies", [])}
    opt_deps = project.get("optional-dependencies", {})

    reachable_optional: set[str] = set()
    for extra in opt_deps:
        for spec in _flatten_extra(opt_deps, extra):
            reachable_optional.add(_dist_name(spec))

    for dist in _PROVIDER_DISTRIBUTIONS:
        d = dist.lower()
        assert d not in core_deps, (
            f"provider SDK {dist!r} must not be a mandatory core dependency; "
            f"core dependencies = {sorted(core_deps)}"
        )
        assert d in reachable_optional, (
            f"provider SDK {dist!r} must be reachable as an optional extra; "
            f"optional extras expose = {sorted(reachable_optional)}"
        )


# ── local/first-class backends are not gated behind an extra ──────────────


def test_local_backends_not_gated(tmp_path):
    """Backends that require no provider extra (docker / apple_container) must
    still resolve through the factory WITHOUT tripping a missing-extra
    ImportError — guarding against an over-gating fix that hides every
    environment behind an optional extra.

    Other runtime failures (no Docker daemon, definition validation, etc.) are
    tolerated; only an ImportError-family failure for these extra-less types is
    a defect.
    """
    from harbor.environments.factory import EnvironmentFactory
    from harbor.models.environment_type import EnvironmentType
    from harbor.models.task.config import EnvironmentConfig

    for type_name in ("DOCKER", "APPLE_CONTAINER"):
        env_type = getattr(EnvironmentType, type_name)
        try:
            EnvironmentFactory.create_environment(
                env_type,
                environment_dir=tmp_path,
                environment_name="verify-env",
                session_id="verify-session",
                trial_paths=_make_trial_paths(tmp_path),
                task_env_config=EnvironmentConfig(),
            )
        except ImportError as exc:
            pytest.fail(
                f"{type_name} requires no extra and must not raise an "
                f"ImportError-family error: {exc!r}"
            )
        except Exception:
            # Any non-import failure (e.g. missing Dockerfile, no daemon) is
            # acceptable: the point is only that the extras gate did not fire.
            pass
