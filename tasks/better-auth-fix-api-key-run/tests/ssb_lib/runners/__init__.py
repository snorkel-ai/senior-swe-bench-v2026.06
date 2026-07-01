"""Multi-language test runners for Senior SWE-Bench verification."""

from pathlib import Path

from .base import BaseRunner as BaseRunner
from .base import RunConfig as RunConfig
from .base import RunResult as RunResult
from .base import TestResult as TestResult

RUNNER_REGISTRY: list[type[BaseRunner]] = []


def register(cls: type[BaseRunner]) -> type[BaseRunner]:
    RUNNER_REGISTRY.append(cls)
    return cls


def discover_and_run_all(
    verify_dir: Path,
    repo_dir: Path,
    logs_dir: Path,
    config: dict | None = None,
) -> dict[str, RunResult]:
    """Discover verify files and run all matching runners, keyed by runner name."""
    verify_dir = Path(verify_dir)
    repo_dir = Path(repo_dir)
    logs_dir = Path(logs_dir)
    config = config or {}

    results: dict[str, RunResult] = {}
    for runner_cls in RUNNER_REGISTRY:
        runner = runner_cls()
        files = runner.discover(verify_dir)
        if not files:
            continue
        # Per-file try/except: a crash on one file must not discard results
        # already accumulated for its siblings.
        for f in files:
            key = f"{runner.name}::{f.stem}" if len(files) > 1 else runner.name
            try:
                run_config = runner.build_config(f, repo_dir, logs_dir, config)
                results[key] = runner.run_file(run_config)
            except Exception as e:
                results[key] = RunResult(
                    error=f"{runner.name} runner crashed on {f.stem}: {e}",
                )
    return results


from . import ast_grep as _ast_grep  # noqa: E402, F401
from . import elixir as _elixir  # noqa: E402, F401
from . import go as _go  # noqa: E402, F401
from . import js as _js  # noqa: E402, F401
from . import python as _python  # noqa: E402, F401
from . import rust as _rust  # noqa: E402, F401
from . import shell as _shell  # noqa: E402, F401
