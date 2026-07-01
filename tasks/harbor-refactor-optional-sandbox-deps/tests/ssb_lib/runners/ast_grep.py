"""ast-grep structural check runner — runs rules from verify_ast.toml."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from . import register
from .base import BaseRunner, RunConfig, RunResult, TestResult


class AstGrepRunConfig(RunConfig):
    """Configuration for ast-grep structural checks."""

    rules: list[dict] = []


@register
class AstGrepRunner(BaseRunner):
    name = "ast_grep"

    def discover(self, verify_dir: Path) -> list[Path]:
        toml_path = verify_dir / "verify_ast.toml"
        return [toml_path] if toml_path.exists() else []

    def build_config(self, test_file: Path, repo_dir: Path, logs_dir: Path, verify_toml: dict) -> AstGrepRunConfig:
        rules = load_rules(test_file)
        return AstGrepRunConfig(test_file=test_file, repo_dir=repo_dir, rules=rules)

    def run_file(self, config: AstGrepRunConfig) -> RunResult:
        return run_rules(config.rules, config.repo_dir)


def run_rules(rules: list[dict], repo_dir: Path) -> RunResult:
    """Run ast-grep rules and return pass/fail for each."""
    if not rules:
        return RunResult(tests=[])

    if importlib.util.find_spec("ast_grep_py") is None:
        return RunResult(
            tests=[],
            error="ast-grep-py not installed (needed for verify_ast.toml)",
        )

    tests: list[TestResult] = []
    for rule in rules:
        target = repo_dir / rule["path"]
        if not target.exists():
            tests.append(
                TestResult(
                    name=rule["name"],
                    passed=False,
                    detail=f"path not found: {rule['path']}",
                )
            )
            continue
        elif target.is_dir():
            matches = _find_in_dir(target, rule["pattern"], rule["lang"])
        else:
            matches = _find_in_file(target, rule["pattern"], rule["lang"])

        expect = rule.get("expect", "some")
        passed = _check_expectation(matches, expect)
        detail = ""
        if not passed:
            detail = f"expected {expect}, found {len(matches)} match(es)"
        tests.append(TestResult(name=rule["name"], passed=passed, detail=detail))

    return RunResult(tests=tests)


def load_rules(toml_path: Path) -> list[dict]:
    """Load rules from verify_ast.toml."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[no-redef]
    data = tomllib.loads(toml_path.read_text())
    return data.get("rule", [])


_LANG_MAP = {
    "rust": "Rust",
    "go": "Go",
    "typescript": "TypeScript",
    "javascript": "JavaScript",
    "python": "Python",
    "c": "C",
    "cpp": "Cpp",
    "zig": None,
    "elixir": None,
}


def _find_in_file(path: Path, pattern: str, lang: str) -> list:
    from ast_grep_py import SgRoot

    sg_lang = _LANG_MAP.get(lang, lang.capitalize())
    if sg_lang is None:
        raise ValueError(f"ast-grep does not support language '{lang}'")
    source = path.read_text()
    try:
        root = SgRoot(source, sg_lang)
    except Exception as e:
        raise ValueError(f"ast-grep error for language '{lang}' ({sg_lang}): {e}") from e
    return root.root().find_all({"rule": {"pattern": pattern}})


def _find_in_dir(directory: Path, pattern: str, lang: str) -> list:
    ext_map = {
        "rust": [".rs"],
        "go": [".go"],
        "typescript": [".ts", ".tsx"],
        "javascript": [".js", ".jsx"],
        "python": [".py"],
        "zig": [".zig"],
        "elixir": [".ex", ".exs"],
        "c": [".c", ".h"],
        "cpp": [".cpp", ".hpp", ".cc"],
    }
    extensions = ext_map.get(lang, [f".{lang}"])
    all_matches = []
    for ext in extensions:
        for f in directory.rglob(f"*{ext}"):
            all_matches.extend(_find_in_file(f, pattern, lang))
    return all_matches


def _check_expectation(matches: list, expect: str | int) -> bool:
    """Compare match count against the rule's ``expect`` directive.

    Recognized values are ``"none"``, ``"some"``, or a non-negative integer
    (string or int). Anything else raises ``ValueError`` to fail closed rather
    than silently degrade into a vacuous ``count > 0`` pass.
    """
    count = len(matches)
    if expect == "none":
        return count == 0
    if expect == "some":
        return count > 0
    # bool is an int subclass but never a valid count; reject so True/False
    # don't slip through as 1/0.
    if isinstance(expect, int) and not isinstance(expect, bool):
        target = expect
    elif isinstance(expect, str) and expect.lstrip("+").isdigit():
        # isdigit rejects floats ("2.5") and signs, accepting integers only.
        target = int(expect)
    else:
        raise ValueError(f"invalid ast-grep expect value {expect!r}: must be 'none', 'some', or a non-negative integer")
    if target < 0:
        raise ValueError(f"invalid ast-grep expect value {expect!r}: integer must be non-negative")
    return count == target
