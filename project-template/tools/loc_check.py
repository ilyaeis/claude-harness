#!/usr/bin/env python3
"""Size gate for Python files, classes and functions. scripts/check.sh and the commit hook run it.

Lines of code (LOC) exclude blank lines, comment-only lines and docstrings. An item over
its limit fails, unless the baseline file lists it and it has not grown. The baseline only
shrinks: a listed item that got smaller or disappeared also fails until
``--update-baseline`` records it. Limits and excludes come from ``[tool.loc_check]`` in
pyproject.toml.
"""

import argparse
import ast
import io
import json
import os
import subprocess
import sys
import tokenize
import tomllib
from dataclasses import dataclass
from pathlib import Path

PYPROJECT_PATH = Path("pyproject.toml")
DEFAULT_LIMITS = {"file": 300, "class": 200, "function": 60}
"""Used when pyproject.toml has no [tool.loc_check] value for a kind."""

NON_CODE_TOKENS = {
    tokenize.NEWLINE,
    tokenize.NL,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENDMARKER,
    tokenize.ENCODING,
    tokenize.COMMENT,
}


@dataclass(frozen=True)
class Config:
    """The settings of one run, from pyproject.toml."""

    file_limit: int
    class_limit: int
    function_limit: int
    exclude: tuple[str, ...]
    """Path prefixes to skip (vendored or generated code). Hidden dirs are skipped too."""
    baseline_path: Path


@dataclass(frozen=True)
class SizedItem:
    """One file, class or function with its LOC and limit."""

    key: str
    """``path``, ``path::Class`` or ``path::Class.method``."""
    loc: int
    limit: int


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config = load_config(PYPROJECT_PATH)
    paths = args.paths or all_python_files(config)
    items = [item for path in paths for item in measure_file(path, config)]
    baseline = load_baseline(config.baseline_path)
    if args.init_baseline:
        over = {k: loc for k, loc in largest_loc_per_key(items).items() if loc > limit_of(k, config)}
        save_baseline(over, config.baseline_path)
        return 0
    if args.update_baseline:
        save_baseline(shrink_baseline(baseline, items, config), config.baseline_path)
        return 0
    problems = find_problems(items, baseline, checks_whole_repo=not args.paths)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Files to check. Default: all.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--update-baseline",
        action="store_true",
        help="Lower or remove baseline entries of items that got smaller.",
    )
    mode.add_argument(
        "--init-baseline",
        action="store_true",
        help="Write a new baseline with every item now over its limit.",
    )
    return parser.parse_args(argv)


def load_config(pyproject: Path) -> Config:
    """Read [tool.loc_check]. A missing file or table gives the defaults."""
    table = {}
    if pyproject.exists():
        table = tomllib.loads(pyproject.read_text()).get("tool", {}).get("loc_check", {})
    return Config(
        file_limit=int(table.get("file", DEFAULT_LIMITS["file"])),
        class_limit=int(table.get("class", DEFAULT_LIMITS["class"])),
        function_limit=int(table.get("function", DEFAULT_LIMITS["function"])),
        exclude=tuple(table.get("exclude", [])),
        baseline_path=Path(table.get("baseline", "tools/loc_baseline.json")),
    )


def measure_file(path: str, config: Config) -> list[SizedItem]:
    """The file and every class and function in it, with their LOC."""
    source = Path(path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        print(f"BLOCK {path}: syntax error, cannot check ({error})")
        return [SizedItem(path, config.file_limit + 1, config.file_limit)]
    code_lines = find_code_lines(source, tree)
    items = [SizedItem(path, len(code_lines), config.file_limit)]
    for node, qualname in walk_definitions(tree):
        loc = sum(1 for n in code_lines if node.lineno <= n <= node.end_lineno)
        items.append(SizedItem(f"{path}::{qualname}", loc, limit_for_node(node, config)))
    return items


def limit_for_node(node: ast.AST, config: Config) -> int:
    return config.class_limit if isinstance(node, ast.ClassDef) else config.function_limit


def find_code_lines(source: str, tree: ast.AST) -> set[int]:
    lines = source.splitlines()
    non_empty = {n for n, line in enumerate(lines, 1) if line.strip()}
    return non_empty - comment_only_lines(source) - docstring_lines(tree)


def comment_only_lines(source: str) -> set[int]:
    """Lines whose only token is a comment (a trailing comment does not count)."""
    code_lines: set[int] = set()
    comment_lines: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            comment_lines.add(token.start[0])
        elif token.type not in NON_CODE_TOKENS:
            code_lines.add(token.start[0])
    return comment_lines - code_lines


def docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        value = node.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            lines.update(range(value.lineno, value.end_lineno + 1))
    return lines


def walk_definitions(tree: ast.AST, prefix: str = ""):
    """Yield every class and function with its dotted name, e.g. ``Class.method``."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            qualname = f"{prefix}{node.name}"
            yield node, qualname
            yield from walk_definitions(node, f"{qualname}.")
        else:
            yield from walk_definitions(node, prefix)


def find_problems(items: list[SizedItem], baseline: dict[str, int], checks_whole_repo: bool) -> list[str]:
    """Items over their limit and not covered by the baseline, and stale baseline entries."""
    problems = [
        describe_oversized(item, baseline.get(item.key))
        for item in items
        if item.loc > max(item.limit, baseline.get(item.key, 0))
    ]
    measured = largest_loc_per_key(items)
    for key, recorded_loc in baseline.items():
        is_measured = key in measured
        if not is_measured and not checks_whole_repo:
            continue
        if not is_measured or measured[key] < recorded_loc:
            problems.append(
                f"STALE baseline entry {key} (recorded {recorded_loc} LOC). "
                f"Run: python3 tools/loc_check.py --update-baseline"
            )
    return problems


def describe_oversized(item: SizedItem, recorded_loc: int | None) -> str:
    if recorded_loc is None:
        return f"BLOCK {item.key} = {item.loc} LOC (limit {item.limit})"
    return f"BLOCK {item.key} = {item.loc} LOC, grew past its baseline of {recorded_loc} (limit {item.limit})"


def shrink_baseline(baseline: dict[str, int], items: list[SizedItem], config: Config) -> dict[str, int]:
    """Lower entries that shrank. Drop entries back under their limit, and entries of
    deleted files or definitions. Keep entries of files this run did not measure."""
    measured = largest_loc_per_key(items)
    measured_files = {item.key.split("::", 1)[0] for item in items}
    shrunk: dict[str, int] = {}
    for key, recorded_loc in baseline.items():
        path = key.split("::", 1)[0]
        if key not in measured:
            if path not in measured_files and Path(path).exists():
                shrunk[key] = recorded_loc
        elif measured[key] > limit_of(key, config):
            shrunk[key] = min(recorded_loc, measured[key])
    return shrunk


def largest_loc_per_key(items: list[SizedItem]) -> dict[str, int]:
    """LOC per key. Two definitions with one name (a property setter) keep the larger."""
    largest: dict[str, int] = {}
    for item in items:
        largest[item.key] = max(item.loc, largest.get(item.key, 0))
    return largest


def limit_of(key: str, config: Config) -> int:
    """The limit of a baseline key: a file, or the class or function it names."""
    if "::" not in key:
        return config.file_limit
    path, qualname = key.split("::", 1)
    for node, name in walk_definitions(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if name == qualname:
            return limit_for_node(node, config)
    return config.function_limit


def load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_baseline(baseline: dict[str, int], path: Path) -> None:
    path.write_text(json.dumps(dict(sorted(baseline.items())), indent=1) + "\n")
    print(f"Wrote {len(baseline)} baseline entries to {path}")


def all_python_files(config: Config) -> list[str]:
    files = python_files_from_git(config)
    return files if files is not None else python_files_from_walk(config)


def python_files_from_git(config: Config) -> list[str] | None:
    """Tracked and new (not ignored) ``*.py`` files, or None outside a git repo."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    names = {line for line in result.stdout.splitlines() if line}
    return sorted(n for n in names if Path(n).exists() and not is_excluded(n, config))


def python_files_from_walk(config: Config) -> list[str]:
    """Every ``*.py`` under the current dir, for a checkout without git."""
    paths: list[str] = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), config)]
        paths.extend(os.path.normpath(os.path.join(root, name)) for name in files if name.endswith(".py"))
    return sorted(paths)


def is_excluded(path: str, config: Config) -> bool:
    norm = os.path.normpath(path)
    parts = norm.split(os.sep)
    if any(part.startswith(".") and part != "." for part in parts) or "__pycache__" in parts:
        return True
    return any(norm == prefix or norm.startswith(prefix + os.sep) for prefix in config.exclude)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
