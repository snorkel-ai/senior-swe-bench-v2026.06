"""Filesystem-only test harness for harbor-add-agent-file-retention stories.

This harness does NOT apply any retention policy and references NO harbor
symbol. It only (1) materializes a directory from a JSON file manifest and
(2) reads back the directory's root file set and subdirectory set. The
validation agent writes the "drive retention" step itself, against the
retention entry point it discovers in the solution source — the harness
stays implementation-agnostic.

Manifest schema (a plain dict, typically parsed from a story input JSON):
  {
    "files":   ["a.json", "b.log", ...],          # files in the ROOT
    "subdirs": {"nested": ["x.json"], "logs": []}  # subdir -> file names
  }
Both keys are optional. Subdir file lists may be empty (an empty subdir).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def populate(target_dir: str | Path, manifest: dict[str, Any]) -> Path:
    """Create the files and subdirectories described by ``manifest``.

    Every file is given small, unique, valid JSON-ish content so nothing
    depends on file contents — only on which paths exist. Returns the
    populated directory as a Path.
    """
    root = Path(target_dir)
    root.mkdir(parents=True, exist_ok=True)

    for name in manifest.get("files", []) or []:
        (root / name).write_text(json.dumps({"name": name}) + "\n")

    for subdir, files in (manifest.get("subdirs", {}) or {}).items():
        sub = root / subdir
        sub.mkdir(parents=True, exist_ok=True)
        for name in files or []:
            (sub / name).write_text(json.dumps({"name": name}) + "\n")

    return root


def snapshot(target_dir: str | Path) -> dict[str, list[str]]:
    """Return the directory's current root file set and subdirectory set.

    {"root_files": sorted[str], "subdirs": sorted[str]}

    ``root_files`` are names of regular files in the root only (not nested).
    ``subdirs`` are names of immediate subdirectories. A missing directory
    reads back as two empty lists.
    """
    root = Path(target_dir)
    if not root.exists():
        return {"root_files": [], "subdirs": []}
    root_files = sorted(p.name for p in root.iterdir() if p.is_file())
    subdirs = sorted(p.name for p in root.iterdir() if p.is_dir())
    return {"root_files": root_files, "subdirs": subdirs}
