"""Shared planner progress and issue helpers."""

from __future__ import annotations

from collections.abc import Callable

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


def issue(path: str, category: str, message: str) -> ReviewIssue:
    return ReviewIssue.from_dict(
        {"path": path, "category": category, "message": message}
    )


__all__ = ["ProgressCallback", "emit", "issue"]
