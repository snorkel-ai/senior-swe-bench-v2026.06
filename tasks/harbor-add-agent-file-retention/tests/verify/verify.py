"""Backward-compatibility verifier (pass-to-pass) for
harbor-add-agent-file-retention.

The new retention control's contract is verified by the validation stories,
which discover the option from the solution and exercise it. This file holds
the complementary, implementation-independent guarantee that the change does
NOT break existing clients: invoking the run launcher as it is used today,
WITHOUT the new option, must keep working.

Pass-to-pass: holds on the unfixed tree (the option does not exist yet) and
on every valid solution (the option is optional, defaulting to
keep-everything). These tests FAIL only if a solution breaks the launcher
surface — an import error, making the new control required, or dropping a
pre-existing launch option existing invocations depend on. nop-gating is
carried by the validation stories, not here.

Runs via `/repo/harbor/.venv/bin/python -m pytest` (see verify.toml),
cwd=/repo/harbor, so `import harbor...` resolves to the editable install.
"""

from __future__ import annotations

from typer.main import get_command
from typer.testing import CliRunner

from harbor.cli.main import app

RUN_COMMAND = "run"

# Pre-existing launch options that existed BEFORE this task; existing
# invocations rely on them, so a valid change must leave them in place.
PRE_EXISTING_OPTIONS = ("--agent", "--model", "--config")

# A wide terminal so rich does not wrap option names across lines.
_WIDE_ENV = {"COLUMNS": "1000", "TERM": "dumb"}


def _run_command():
    group = get_command(app)
    return getattr(group, "commands", {}).get(RUN_COMMAND)


def test_run_launcher_help_still_works() -> None:
    """The run launcher remains invocable and `run --help` renders (exit 0).

    Holds on nop and on any valid fix; fails only if the launcher is broken
    (e.g. an import error or a malformed option added during the change).
    """
    result = CliRunner().invoke(app, [RUN_COMMAND, "--help"], env=_WIDE_ENV)
    assert result.exit_code == 0, (
        f"`{RUN_COMMAND} --help` must exit 0 so existing usage keeps working; "
        f"got exit {result.exit_code}.\n{result.output}"
    )


def test_run_command_keeps_preexisting_options() -> None:
    """The run command is still registered with its pre-existing launch options.

    Existing invocations pass these options, so they must survive the change
    (true on nop, and on any valid fix that only ADDS a retention option).
    """
    run_cmd = _run_command()
    assert run_cmd is not None, (
        "The 'run' command must remain registered on the harbor CLI app."
    )
    opt_strings = {
        opt for param in run_cmd.params for opt in getattr(param, "opts", [])
    }
    missing = [o for o in PRE_EXISTING_OPTIONS if o not in opt_strings]
    assert not missing, (
        "Pre-existing run launch options must still be present (existing usage "
        f"depends on them); missing: {missing}."
    )
