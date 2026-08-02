"""Shared planner progress and issue helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ..domain.issues import ReviewIssue


ProgressCallback = Callable[[str, int, int, str], None]


def emit(
    callback: ProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    path: str,
) -> None:
    if callback:
        callback(stage, current, total, path)


def typed_issues(values: Iterable[dict | ReviewIssue]) -> list[ReviewIssue]:
    return [
        value if isinstance(value, ReviewIssue) else ReviewIssue.from_dict(value)
        for value in values
    ]


def issue(path: str, category: str, message: str) -> ReviewIssue:
    return ReviewIssue.from_dict(
        {"path": path, "category": category, "message": message}
    )


__all__ = ["ProgressCallback", "emit", "issue", "typed_issues"]
