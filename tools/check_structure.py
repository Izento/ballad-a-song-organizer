"""Enforce Ballad's function-size and nesting discipline."""

from __future__ import annotations

import ast
from pathlib import Path

MAX_FUNCTION_LINES = 60
MAX_NESTING_DEPTH = 5
SOURCE_DIRECTORIES = ("renamer", "gui", "cli", "src")
NESTING_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.ExceptHandler,
    ast.Match,
)


def _nested_depth(node: ast.AST, depth: int = 0) -> int:
    """Return the deepest control-flow nesting below one function."""
    deepest = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        child_depth = depth + isinstance(child, NESTING_NODES)
        deepest = max(deepest, _nested_depth(child, child_depth))
    return deepest


def _violations(path: Path) -> list[str]:
    """Return structure violations for one Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        length = node.end_lineno - node.lineno + 1
        depth = _nested_depth(node)
        location = f"{path}:{node.lineno} {node.name}"
        if length > MAX_FUNCTION_LINES:
            violations.append(f"{location}: {length} lines (maximum {MAX_FUNCTION_LINES})")
        if depth > MAX_NESTING_DEPTH:
            violations.append(f"{location}: nesting depth {depth} (maximum {MAX_NESTING_DEPTH})")
    return violations


def main() -> int:
    """Check production source and report every structural violation."""
    violations = [
        violation
        for directory in SOURCE_DIRECTORIES
        for path in Path(directory).rglob("*.py")
        for violation in _violations(path)
    ]
    if not violations:
        return 0
    print("\n".join(violations))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
