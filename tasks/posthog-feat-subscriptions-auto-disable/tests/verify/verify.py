"""Nop-gating verifier for posthog-feat-subscriptions-auto-disable.

AST-parse ``ee/api/subscription.py``, locate the ``SubscriptionSerializer`` and
assert its ``Meta.fields`` list exposes an ``enabled`` field on the API. The
pre-fix serializer has no enabled/disabled concept (only the unrelated
``summary_enabled`` flag), so the nop tree fails this check. The task contract
commits to the ``enabled`` field name at the API; a ModelSerializer lists every
exposed field (whether backed by a model field named ``enabled`` or a
differently-named field via ``source=``) in ``Meta.fields``. The pre-existing
``summary_enabled`` field is excluded so a substring match on "enabled" cannot
pass the nop.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path("/repo/posthog")
SERIALIZER_FILE = REPO / "ee" / "api" / "subscription.py"

SERIALIZER_CLASS = "SubscriptionSerializer"
REQUIRED_FIELD = "enabled"
# Pre-existing field whose name contains "enabled" — must NOT satisfy the gate.
PREEXISTING_DECOY = "summary_enabled"


def _get_class_def(node: ast.AST, name: str) -> ast.ClassDef | None:
    for child in ast.walk(node):
        if isinstance(child, ast.ClassDef) and child.name == name:
            return child
    return None


def _meta_fields(serializer: ast.ClassDef) -> list[str] | None:
    """Return the string elements of ``Meta.fields`` for the serializer, or
    None if no ``Meta`` class with a ``fields`` list assignment is found.
    """
    meta = next(
        (n for n in serializer.body if isinstance(n, ast.ClassDef) and n.name == "Meta"),
        None,
    )
    if meta is None:
        return None
    for stmt in meta.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "fields" for t in stmt.targets
        ):
            value = stmt.value
            if isinstance(value, (ast.List, ast.Tuple)):
                return [
                    elt.value
                    for elt in value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    return None


def test_subscription_serializer_exposes_enabled_field() -> None:
    """The subscriptions API must expose an ``enabled`` field post-change."""
    assert SERIALIZER_FILE.exists(), f"Missing module: {SERIALIZER_FILE}"

    tree = ast.parse(SERIALIZER_FILE.read_text())
    serializer = _get_class_def(tree, SERIALIZER_CLASS)
    assert serializer is not None, (
        f"{SERIALIZER_CLASS} not found in {SERIALIZER_FILE}. "
        "The subscriptions serializer must remain in this module."
    )

    fields = _meta_fields(serializer)
    assert fields is not None, (
        f"{SERIALIZER_CLASS}.Meta.fields not found as a list/tuple in "
        f"{SERIALIZER_FILE}."
    )

    # Sanity: we found the real serializer (it exposes the pre-existing decoy).
    assert PREEXISTING_DECOY in fields, (
        f"{SERIALIZER_CLASS}.Meta.fields looks unexpected — got {fields!r}. "
        f"Expected the pre-existing fields (including {PREEXISTING_DECOY!r})."
    )

    assert REQUIRED_FIELD in fields, (
        f"{SERIALIZER_CLASS} does not expose an `{REQUIRED_FIELD}` field in "
        f"{SERIALIZER_FILE} (Meta.fields = {fields!r}). The pause/auto-disable "
        f"lifecycle must surface `{REQUIRED_FIELD}` on the API, distinct from "
        f"the pre-existing `{PREEXISTING_DECOY}` flag."
    )
