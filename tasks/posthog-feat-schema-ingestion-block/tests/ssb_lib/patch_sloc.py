"""Compute SLOC metrics from unified diffs.

Uses ``unidiff`` for diff parsing and ``pygments`` for language-aware comment
detection.

Limitation — block comments: each changed line is tokenized in isolation, so a
prose line inside ``/* */`` without a comment-like prefix (``*``, ``#``, ``//``)
is miscounted as code. This is inherent to diff-only analysis without full file
context; in practice the impact is small.
"""

import unidiff
from pygments.lexer import Lexer
from pygments.lexers import get_lexer_for_filename
from pygments.token import Comment, String, Token
from pygments.util import ClassNotFound


def _lexer_for_path(path: str) -> Lexer | None:
    """Return a Pygments lexer for *path*, or None if unrecognised."""
    try:
        return get_lexer_for_filename(path, stripnl=False)
    except ClassNotFound:
        return None


def _is_code_line(text: str, lexer: Lexer | None) -> bool:
    """Return True if *text* is a non-blank, non-comment source line.

    When a Pygments lexer is available, tokenizes the line and checks whether
    any token is NOT a comment or whitespace.  Falls back to a simple
    blank-check when the language is unknown.
    """
    stripped = text.strip()
    if not stripped:
        return False

    if lexer is None:
        return True  # unknown language — count all non-blank lines

    try:
        tokens = list(lexer.get_tokens(stripped))
    except Exception:  # noqa: BLE001
        return True  # tokenization failure — count conservatively

    for ttype, value in tokens:
        if not value.strip():
            continue
        if ttype in Token.Text or ttype in Comment or ttype is String.Doc:
            continue
        return True

    return False


def compute_patch_sloc(patch_text: str) -> dict:
    """Compute SLOC, file count, and hunk count from a unified diff.

    Returns ``{"sloc": int, "files": int, "hunks": int}``.

    * **sloc** — non-blank, non-comment added + removed lines
    * **files** — number of files modified
    * **hunks** — total hunk count across all files
    """
    try:
        patch_set = unidiff.PatchSet(patch_text)
    except unidiff.errors.UnidiffParseError:
        # An unparseable diff is NOT an empty diff (0 SLOC). Flag it so callers
        # treat the patch as UNMEASURED rather than a perfect 0-SLOC change.
        return {"sloc": 0, "files": 0, "hunks": 0, "parse_error": True}

    sloc = 0
    files = len(patch_set)
    hunks = sum(len(pf) for pf in patch_set)

    for patched_file in patch_set:
        lexer = _lexer_for_path(patched_file.path)
        for hunk in patched_file:
            for line in hunk:
                if (line.is_added or line.is_removed) and _is_code_line(line.value, lexer):
                    sloc += 1

    return {"sloc": sloc, "files": files, "hunks": hunks}
