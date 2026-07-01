"""Centralized read-only filesystem tools + the agentic explore-and-emit loop.

Shared by the rubric and taste judges. litellm normalizes the *completion* API
across providers but ships no agent tools, so we declare our own: each tool is a
``Tool`` (its OpenAI-function schema + an ``impl``), registered by name.
``handle_tool_call`` dispatches through that registry — adding a tool is one
declaration, never a new branch.

Read-only by construction: the only side effects are reading files, listing
directories, and grep. Truncation caps bound how much any single call returns.
The judge explores with these tools and then submits a structured result via a
terminal "emit" tool — all in one litellm message thread, so the files it reads
stay in context through scoring.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import llm_utils

if TYPE_CHECKING:
    from pydantic import BaseModel

MAX_FILE_LINES = 300
MAX_DIR_ENTRIES = 100
MAX_SEARCH_RESULTS = 50


@dataclass(frozen=True)
class Tool:
    """A read-only exploration tool: its schema plus the implementation.

    ``impl`` is called as ``impl(args, repo_path, max_file_lines)`` and returns
    the result text shown to the model.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    impl: Callable[[dict[str, Any], Path, int], str]

    @property
    def spec(self) -> dict[str, Any]:
        """OpenAI-format tool entry for the litellm request."""
        return llm_utils.openai_tool(self.name, self.description, self.parameters)


# ── Tool implementations ────────────────────────────────────────────────────


def _read_file(args: dict[str, Any], repo_path: Path, max_file_lines: int) -> str:
    fpath = repo_path / args["path"]
    if not fpath.exists():
        return f"[error: file not found: {args['path']}]"
    try:
        lines = fpath.read_text(errors="replace").splitlines()
    except OSError as e:
        return f"[error reading file: {e}]"
    start = max(0, args.get("start_line", 1) - 1)
    selected = lines[start : args.get("end_line", len(lines))]
    if len(selected) > max_file_lines:
        extra = len(selected) - max_file_lines
        selected = [*selected[:max_file_lines], f"... (truncated, {extra} more lines)"]
    return "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(selected))


def _list_directory(args: dict[str, Any], repo_path: Path, max_file_lines: int) -> str:
    del max_file_lines  # not applicable to directory listings
    dpath = repo_path / args["path"]
    if not dpath.exists():
        return f"[error: directory not found: {args['path']}]"
    try:
        entries = sorted(dpath.iterdir())
    except OSError as e:
        return f"[error listing directory: {e}]"
    result = [("d " if e.is_dir() else "f ") + e.name for e in entries[:MAX_DIR_ENTRIES]]
    if len(entries) > MAX_DIR_ENTRIES:
        result.append(f"... ({len(entries) - MAX_DIR_ENTRIES} more entries)")
    return "\n".join(result)


def _search_code(args: dict[str, Any], repo_path: Path, max_file_lines: int) -> str:
    del max_file_lines  # search is line-oriented, not file-windowed
    pattern = args["pattern"]
    file_glob = args.get("file_glob", "")
    cmd = (
        ["grep", "-rn", "--include", file_glob, pattern, str(repo_path)]
        if file_glob
        else ["grep", "-rn", pattern, str(repo_path)]
    )
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (subprocess.SubprocessError, OSError) as e:
        return f"[error searching: {e}]"
    all_lines = out.stdout.splitlines()
    prefix = str(repo_path) + "/"
    cleaned = [ln.replace(prefix, "", 1) for ln in all_lines[:MAX_SEARCH_RESULTS]]
    if len(all_lines) > MAX_SEARCH_RESULTS:
        cleaned.append(f"... ({len(all_lines) - MAX_SEARCH_RESULTS} more matches)")
    return "\n".join(cleaned) if cleaned else "(no matches)"


READ_FILE = Tool(
    "read_file",
    "Read a file from the repository. Returns file content with line numbers. "
    "Use to examine source files for coding patterns, style, conventions.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to repo root."},
            "start_line": {"type": "integer", "description": "Start line (1-based). Optional."},
            "end_line": {"type": "integer", "description": "End line (inclusive). Optional."},
        },
        "required": ["path"],
    },
    _read_file,
)

LIST_DIRECTORY = Tool(
    "list_directory",
    "List files and directories. Use to discover project structure, find sibling modules, locate config files.",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path relative to repo root. '.' for root."},
        },
        "required": ["path"],
    },
    _list_directory,
)

SEARCH_CODE = Tool(
    "search_code",
    "Search for a regex pattern in the codebase. Returns matching lines with file paths. "
    "Use to find usage patterns and conventions.",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for."},
            "file_glob": {"type": "string", "description": "File glob filter (e.g. '*.py'). Optional."},
        },
        "required": ["pattern"],
    },
    _search_code,
)

_REGISTRY: dict[str, Tool] = {t.name: t for t in (READ_FILE, LIST_DIRECTORY, SEARCH_CODE)}

# Tool sets (OpenAI specs): the rubric judge explores with read+list; taste adds search.
READ_TOOLS = [READ_FILE.spec, LIST_DIRECTORY.spec]
EXPLORE_TOOLS = [READ_FILE.spec, LIST_DIRECTORY.spec, SEARCH_CODE.spec]


def handle_tool_call(name: str, args: dict[str, Any], repo_path: Path, *, max_file_lines: int = MAX_FILE_LINES) -> str:
    """Dispatch a read-only tool call against ``repo_path`` via the registry."""
    tool = _REGISTRY.get(name)
    if tool is None:
        return f"[unknown tool: {name}]"
    return tool.impl(args, repo_path, max_file_lines)


# ── Explore-and-emit loop ─────────────────────────────────────────────────────


def _loads(arguments: str) -> dict[str, Any]:
    try:
        return json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return {}


def _describe_call(call: Any) -> str:
    """One-line description of a tool call for the stderr trace (what was examined)."""
    name = getattr(call.function, "name", "?")
    args = _loads(getattr(call.function, "arguments", "") or "")
    if name == "read_file":
        sl, el = args.get("start_line"), args.get("end_line")
        rng = f"[{sl or ''}-{el or ''}]" if (sl or el) else ""
        return f"read_file: {args.get('path', '?')}{rng}"
    if name == "list_directory":
        return f"list_directory: {args.get('path', '?')}"
    if name == "search_code":
        return f"search_code: {args.get('pattern', '?')}"
    return name


def _assistant_message(msg: Any) -> dict[str, Any]:
    """Re-serialize a litellm assistant message (incl. tool_calls) as a plain dict."""
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tcs
        ]
    return out


def run_explore_and_emit(  # noqa: PLR0913
    *,
    model: str,
    system: str,
    user: str,
    repo_path: Path,
    explore_tools: list[dict[str, Any]],
    emit_tool_name: str,
    emit_schema: type[BaseModel],
    max_turns: int,
    max_tokens: int = 2048,
    max_file_lines: int = MAX_FILE_LINES,
    min_explore_turns: int = 0,
    label: str = "explore",
) -> Any | None:
    """Drive one explore→emit thread; return a validated ``emit_schema`` or ``None``.

    The model explores with ``explore_tools`` (read-only) and signals completion
    by calling ``emit_tool_name``, whose parameters are ``emit_schema``'s JSON
    schema. Scoring happens in-thread, so every file read stays in context.

    ``min_explore_turns`` enforces a floor: an emit attempted before that many
    turns is not accepted — the model is nudged to keep exploring (so it can't
    score blind). If the turn budget is exhausted without an emit, one final
    call forces it.
    """
    emit_tool = llm_utils.openai_tool(emit_tool_name, "Submit your final result.", emit_schema.model_json_schema())
    tools = [*explore_tools, emit_tool]
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

    for turn in range(max_turns):
        response = llm_utils.complete(model=model, system=system, messages=messages, tools=tools, max_tokens=max_tokens)
        message = response.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        print(f"  [{label} turn {turn}] tools={len(tool_calls)}", file=sys.stderr)
        for call in tool_calls:
            print(f"      {_describe_call(call)}", file=sys.stderr)

        emitted = any(c.function.name == emit_tool_name for c in tool_calls)
        if emitted and turn >= min_explore_turns:
            args = llm_utils.tool_call_args(response, emit_tool_name)
            if args is not None:
                return emit_schema.model_validate(args)
            # Emit call with unparseable arguments: don't crash on
            # model_validate(None). Fall through to nudge the model to re-emit.

        if not tool_calls:
            if turn < min_explore_turns:
                # Text-only response before the exploration floor. Falling through
                # to the forced emit here would let the model score blind, defeating
                # min_explore_turns — so nudge it to actually explore instead.
                messages.append(_assistant_message(message))
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Explore the codebase with the read-only tools first "
                            f"(at least {min_explore_turns} steps) before scoring, "
                            f"then call {emit_tool_name}."
                        ),
                    }
                )
                continue
            break  # text-only response at/after the floor; fall through to forced emit

        # Reply to every tool call (a too-early emit gets nudged back to exploring;
        # real explore calls are executed) so the thread stays valid.
        messages.append(_assistant_message(message))
        for call in tool_calls:
            if call.function.name == emit_tool_name:
                content = (
                    f"Explore the codebase with the read-only tools before scoring "
                    f"(at least {min_explore_turns} steps), then call {emit_tool_name}."
                )
            else:
                content = handle_tool_call(
                    call.function.name, _loads(call.function.arguments), repo_path, max_file_lines=max_file_lines
                )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

        if turn >= max_turns - 2:
            messages.append(
                {"role": "user", "content": f"You are low on exploration turns. Call {emit_tool_name} now."}
            )

    # Turns exhausted (or text-only stop): force the emit tool in-thread.
    print(f"  [{label}] forcing emit in-context", file=sys.stderr)
    messages.append({"role": "user", "content": f"Call {emit_tool_name} now with your result."})
    response = llm_utils.complete(
        model=model,
        system=system,
        messages=messages,
        tools=[emit_tool],
        tool_choice={"type": "function", "function": {"name": emit_tool_name}},
        max_tokens=max_tokens,
    )
    emit_args = llm_utils.tool_call_args(response, emit_tool_name)
    return emit_schema.model_validate(emit_args) if emit_args is not None else None
