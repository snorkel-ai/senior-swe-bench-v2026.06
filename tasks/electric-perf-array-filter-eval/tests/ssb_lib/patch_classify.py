"""Path-aware classifier for filtering non-behavioral files from patches.

Uses a small LLM to label each file in a patch as BEHAVIORAL / TEST / DOC /
FORMATTING, based on the file's path and add/remove counts, so SLOC and
minimality comparisons stay fair when an agent adds test/doc/formatter churn.

Fail-open: on any error the file is treated as BEHAVIORAL, so a classifier
failure never inflates the agent's bloat ratio.
"""

from __future__ import annotations

import sys
from enum import Enum
from typing import Any

import unidiff
from pydantic import BaseModel, Field


class FileCategory(str, Enum):
    BEHAVIORAL = "behavioral"
    TEST = "test"
    DOC = "doc"
    FORMATTING = "formatting"


class FileClassification(BaseModel):
    path: str
    category: FileCategory
    rationale: str = Field(..., description="One short sentence justifying the category.")


class _PatchClassifications(BaseModel):
    """Structured-output wrapper — the forced tool emits a top-level object."""

    files: list[FileClassification]


SYSTEM_PROMPT = """You classify files in a unified diff so an evaluation
pipeline can compare an oracle (hand-curated, behavioral-only) patch against
an agent-produced raw `git diff`.

For each file, choose the category that best describes its role:

- behavioral: production source — runtime logic, types, configs loaded at
  runtime, build manifests that affect shipped output, dependency manifests
  when adding a runtime dep. Examples: src/foo.py, lib/handler.go,
  internal/auth.rs, app/models/user.rb, Dockerfile.
- test: test code, test fixtures, test snapshots, test infrastructure.
  Examples: tests/test_foo.py, foo_test.go, __tests__/foo.test.tsx,
  spec/foo_spec.rb, e2e/login.cy.ts, conftest.py, jest.config.js,
  tests/fixtures/sample.json.
- doc: human-facing prose, and any standalone markdown/text file that is
  not part of the source build. Examples: README.md, CHANGELOG.md, *.rst,
  docs/**/*.md, ADR files, and agent-authored scratch files such as plan
  or session notes a coding agent wrote into the working tree (e.g.
  logs/agent/sessions/plans/*.md, *.plan.md, NOTES.md, a stray top-level
  *.md describing the change). A markdown or .txt file is `doc` no matter
  where it sits in the tree, UNLESS it is consumed by the build/runtime
  (e.g. a *.md compiled into docs output by a configured pipeline, or a
  prompt template the code loads at runtime). Inline docstring edits
  inside a source file do NOT make that file `doc` — only files whose
  entire purpose is prose.
- formatting: lockfiles, generated code, vendored deps, pure-whitespace
  edits. Examples: package-lock.json, pnpm-lock.yaml, go.sum, Cargo.lock,
  *.pb.go, *_pb2.py, vendor/**.

Edge-case guidance:
- docs/conf.py is behavioral (Sphinx config — runtime Python).
- A path containing "test" can still be behavioral if it implements
  production test-running infrastructure rather than a unit test.
- Test fixtures (tests/fixtures/*.json) are test by convention.
- An agent-authored plan/notes markdown captured in the diff (anything
  under logs/, .claude/, or a stray *.md describing the work) is `doc` —
  it is never production source, even though it may quote code or mention
  source files in its prose.
- When uncertain about CODE files, default to behavioral. Conservative
  defaults never unfairly favour the agent in a downstream SLOC
  comparison. (This does not override the rule above: a standalone prose
  markdown/text file is `doc`, not behavioral.)

You receive only the file path, a flag for new/rename, and the
add/remove counts. Use those signals plus the path conventions of the
file's likely ecosystem (Python, Go, TypeScript, Rust, Ruby, etc.) to
decide. Output one classification per input file with a one-sentence
rationale."""


_USER_TEMPLATE = """Classify each of the following files:

{files_block}

Return one entry per file in the same order. Keep each rationale to one short sentence."""


def _summarise_patch(patch_text: str) -> list[dict[str, Any]] | None:
    """Per-file metadata from a unified diff, or None on parse failure."""
    try:
        patch_set = unidiff.PatchSet(patch_text)
    except unidiff.errors.UnidiffParseError:
        return None
    return [
        {
            "path": pf.path,
            "additions": pf.added,
            "deletions": pf.removed,
            "is_new": pf.is_added_file,
            "is_rename": pf.is_rename,
        }
        for pf in patch_set
    ]


def _format_files_for_prompt(files: list[dict[str, Any]]) -> str:
    lines = []
    for i, f in enumerate(files, 1):
        flags = []
        if f["is_new"]:
            flags.append("new")
        if f["is_rename"]:
            flags.append("rename")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(f"{i}. {f['path']}{flag_str} (+{f['additions']} -{f['deletions']})")
    return "\n".join(lines)


def _all_behavioral(files: list[dict[str, Any]], rationale: str) -> list[FileClassification]:
    return [FileClassification(path=f["path"], category=FileCategory.BEHAVIORAL, rationale=rationale) for f in files]


def classify_patch(
    patch_text: str,
    *,
    model: str,
    max_tokens: int = 4096,
) -> list[FileClassification]:
    """Classify every file in *patch_text*. One LLM call per patch.

    Returns one entry per file in diff order. Fail-open: all-BEHAVIORAL on any
    error.
    """
    files = _summarise_patch(patch_text)
    if files is None or not files:
        return []

    prompt = _USER_TEMPLATE.format(files_block=_format_files_for_prompt(files))

    from . import llm_utils

    try:
        parsed = llm_utils.complete_structured(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            schema=_PatchClassifications,
            tool_name="classify_files",
            tool_description="Classify each file in the diff by its role.",
        )
    except Exception as e:  # noqa: BLE001
        print(f"Patch classifier API error: {e}", file=sys.stderr)
        return _all_behavioral(files, "classifier API error; defaulted to behavioral")

    if parsed is None:
        print("Patch classifier: no parsed output", file=sys.stderr)
        return _all_behavioral(files, "classifier returned no parsed output; defaulted to behavioral")

    by_path = {c.path: c for c in parsed.files}
    out: list[FileClassification] = []
    for f in files:
        cls = by_path.get(f["path"])
        if cls is None:
            out.append(
                FileClassification(
                    path=f["path"],
                    category=FileCategory.BEHAVIORAL,
                    rationale="classifier omitted this file; defaulted to behavioral",
                )
            )
        else:
            out.append(cls)
    return out


def filter_patch_to_behavioral(
    patch_text: str,
    classifications: list[FileClassification],
) -> str:
    """Return *patch_text* with non-BEHAVIORAL files removed.

    Round-trips through unidiff so the output is a valid unified diff
    (or empty string if every file was filtered out).
    """
    try:
        patch_set = unidiff.PatchSet(patch_text)
    except unidiff.errors.UnidiffParseError:
        return patch_text  # fail-open: return original

    keep_paths = {c.path for c in classifications if c.category == FileCategory.BEHAVIORAL}
    kept = [pf for pf in patch_set if pf.path in keep_paths]
    if not kept:
        return ""
    return "".join(str(pf) for pf in kept)


def dropped_paths(classifications: list[FileClassification]) -> list[str]:
    """Paths whose category is not BEHAVIORAL."""
    return [c.path for c in classifications if c.category != FileCategory.BEHAVIORAL]
