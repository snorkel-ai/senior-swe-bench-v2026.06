"""Nop-gating verifier for posthog-feat-approval-gating.

AST-checks that the ``@approval_gate(...)`` call in
``posthog/api/feature_flag.py`` lists more than 2 action entries. The
check accepts any argument form (literals, constants, variables) so it
never rejects a valid alternative implementation.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path("/repo/posthog")
FEATURE_FLAG_API = REPO / "posthog" / "api" / "feature_flag.py"

PRE_FIX_DECORATOR_ARG_COUNT = 2  # "feature_flag.enable", "feature_flag.disable"


def _count_elements(node: ast.AST) -> int:
    """Count elements in a list/tuple AST node, including non-literal entries."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return len(node.elts)
    # Single argument (not wrapped in a list)
    return 1


def test_decorator_gates_more_than_two_actions() -> None:
    """The @approval_gate(...) call lists more than 2 action entries.

    Accepts any argument form: string literals, named constants,
    variables, or a mix. Only checks that the total element count grew
    beyond the pre-fix baseline of 2.
    """
    assert FEATURE_FLAG_API.exists(), f"Missing module: {FEATURE_FLAG_API}"

    source = FEATURE_FLAG_API.read_text()
    tree = ast.parse(source)

    # Find every Call node that looks like approval_gate(<arg>).
    decorator_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "approval_gate"
    ]

    assert decorator_calls, (
        f"@approval_gate(...) decorator not found in {FEATURE_FLAG_API}. "
        "The new action must be wired into the existing decorator."
    )

    max_args = 0
    for call in decorator_calls:
        if not call.args:
            continue
        max_args = max(max_args, _count_elements(call.args[0]))

    assert max_args > PRE_FIX_DECORATOR_ARG_COUNT, (
        f"Expected the @approval_gate(...) call in {FEATURE_FLAG_API} to "
        f"list more than {PRE_FIX_DECORATOR_ARG_COUNT} action entries "
        f"(pre-fix has 'feature_flag.enable' and 'feature_flag.disable'). "
        f"Found {max_args} element(s) in the largest call."
    )



