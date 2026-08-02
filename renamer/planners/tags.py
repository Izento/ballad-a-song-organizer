"""Tag-audit planner adapter."""

from __future__ import annotations

from ..tag_audit import audit_tags_for_folder
from .common import ProgressCallback, emit


def plan_tag_updates(
    folder_path: str,
    recursive: bool = True,
    progress: ProgressCallback | None = None,
    cancel_event=None,
):
    return audit_tags_for_folder(
        folder_path,
        recursive=recursive,
        progress=(
            lambda current, total, path: emit(
                progress,
                "tag-audit",
                current,
                total,
                path,
            )
        )
        if progress
        else None,
        cancel_event=cancel_event,
    )


__all__ = ["plan_tag_updates"]
