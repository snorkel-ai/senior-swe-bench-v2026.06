"""Structural sanity verifier for posthog-feat-llma-enable-tagger.

This file deliberately does NOT nop-gate structurally. The write-contract
split can be implemented many valid ways (per-action serializers +
``get_serializer_class``; a single serializer branching on the view action;
field-stripping in ``get_fields``/``__init__``; rejecting the nested field
from the parent ``validate``). Any structural marker matching one specific
shape would reject a valid alternative, so the only checks here are true of
EVERY valid implementation (and of the pre-fix code): the taggers API module
still parses and still defines the public ``TaggerViewSet``.
"""

from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path("/repo/posthog")
TAGGERS_API = REPO / "products" / "llm_analytics" / "backend" / "api" / "taggers.py"


def _module_tree() -> ast.Module:
    assert TAGGERS_API.exists(), f"Missing pre-existing module: {TAGGERS_API}"
    source = TAGGERS_API.read_text()
    # Parsing also asserts the file is syntactically valid Python.
    return ast.parse(source)


def test_taggers_api_module_parses() -> None:
    """The pre-existing taggers API module is valid Python."""
    tree = _module_tree()
    assert isinstance(tree, ast.Module)


def test_tagger_viewset_still_defined() -> None:
    """The pre-existing public ``TaggerViewSet`` is still defined.

    This class is the stable interface registered at
    ``/api/environments/{team_id}/taggers/``; every valid implementation keeps
    it. Its removal would mean the endpoint the validation stories exercise no
    longer exists.
    """
    tree = _module_tree()
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert "TaggerViewSet" in class_names, (
        "Expected the pre-existing TaggerViewSet class to remain defined in "
        f"{TAGGERS_API}; found classes: {sorted(class_names)}"
    )
