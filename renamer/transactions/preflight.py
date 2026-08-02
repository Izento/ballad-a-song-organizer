"""Read-only validation before any reviewed mutation."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import shutil

from ..cover_art import ArtworkRef, verify_artwork
from ..review_models import (
    ApplyResult,
    RenameProposal,
    ReviewPlan,
    TagProposal,
    path_key,
)
from ..tag_writer import supports_tag_writing
from .errors import ApplyBlocked


def blocked_result(
    item_id: str,
    path: str,
    message: str,
) -> ApplyResult:
    return ApplyResult(
        proposal_id=item_id,
        status="blocked",
        path=path,
        message=message,
        error_type="ApplyBlocked",
    )


def selected_proposals(
    plan: ReviewPlan,
    selected_ids: Iterable[str],
) -> tuple[list[RenameProposal], list[TagProposal]]:
    selected = set(selected_ids)
    renames = [
        item for item in plan.rename_proposals if item.id in selected
    ]
    tags = [item for item in plan.tag_proposals if item.id in selected]
    unknown = selected - {item.id for item in (*renames, *tags)}
    if unknown:
        raise ApplyBlocked(
            f"Unknown proposal IDs: {', '.join(sorted(unknown))}"
        )
    ineligible = [
        item.id
        for item in (*renames, *tags)
        if not item.apply_eligible
    ]
    if ineligible:
        raise ApplyBlocked(
            f"Ineligible proposal IDs: {', '.join(sorted(ineligible))}"
        )
    return renames, tags


def _validate_sources(
    renames: list[RenameProposal],
    tags: list[TagProposal],
    block,
) -> None:
    for item in renames:
        if not os.path.isfile(item.old_path):
            block(
                item.id,
                item.old_path,
                f"Source file is missing: {item.old_path}",
            )
        elif not item.snapshot.matches(item.old_path):
            block(
                item.id,
                item.old_path,
                f"Source changed since analysis: {item.old_path}",
            )
    for item in tags:
        if not os.path.isfile(item.path):
            block(item.id, item.path, f"Tag source is missing: {item.path}")
        elif not item.snapshot.matches(item.path):
            block(
                item.id,
                item.path,
                f"Tag source changed since analysis: {item.path}",
            )
        elif not supports_tag_writing(item.path):
            extension = Path(item.path).suffix.lower() or "this file type"
            block(
                item.id,
                item.path,
                f"Tag writing is not supported for {extension} files.",
            )
        elif item.artwork_after:
            try:
                artwork = ArtworkRef(**item.artwork_after.to_dict())
            except (TypeError, ValueError) as exc:
                block(item.id, item.path, f"Invalid staged artwork: {exc}")
            else:
                if not verify_artwork(artwork):
                    block(
                        item.id,
                        item.path,
                        "Staged artwork is missing or changed.",
                    )


def _validate_unique_sources(
    renames: list[RenameProposal],
    tags: list[TagProposal],
    blocked: dict[str, ApplyResult],
    block,
) -> None:
    groups = (
        (
            renames,
            lambda item: item.old_path,
            "Multiple rename proposals target one source file.",
        ),
        (
            tags,
            lambda item: item.path,
            "Multiple tag proposals target one source file.",
        ),
    )
    for items, source, message in groups:
        by_source = {}
        for item in items:
            if item.id not in blocked:
                by_source.setdefault(path_key(source(item)), []).append(item)
        for duplicates in by_source.values():
            if len(duplicates) > 1:
                for item in duplicates:
                    block(item.id, source(item), message)


def _validate_temporary_space(
    tags: list[TagProposal],
    blocked: dict[str, ApplyResult],
    block,
) -> None:
    eligible = [item for item in tags if item.id not in blocked]
    if not eligible:
        return
    try:
        required = max(os.path.getsize(item.path) for item in eligible)
        free = min(
            shutil.disk_usage(Path(item.path).parent).free
            for item in eligible
        )
    except OSError as exc:
        message = f"Cannot inspect temporary write space: {exc}"
        for item in eligible:
            block(item.id, item.path, message)
        return
    if free < required:
        message = (
            "Insufficient free space for one temporary tag write "
            f"({required} bytes needed)"
        )
        for item in eligible:
            block(item.id, item.path, message)


def _validate_destinations(
    renames: list[RenameProposal],
    blocked: dict[str, ApplyResult],
    block,
) -> None:
    destinations = {}
    for item in renames:
        if item.id in blocked:
            continue
        key = path_key(item.new_path)
        if key in destinations:
            other = destinations[key]
            message = f"Multiple selected proposals target {item.new_path}"
            block(other.id, other.old_path, message)
            block(item.id, item.old_path, message)
        else:
            destinations[key] = item
    changed = True
    while changed:
        changed = False
        source_keys = {
            path_key(item.old_path)
            for item in renames
            if item.id not in blocked
        }
        for item in renames:
            if item.id in blocked:
                continue
            parent = Path(item.new_path).parent
            if not parent.is_dir():
                block(
                    item.id,
                    item.old_path,
                    f"Destination folder does not exist: {parent}",
                )
                changed = True
                continue
            try:
                existing = {
                    path_key(str(candidate))
                    for candidate in parent.iterdir()
                    if candidate.exists()
                }
            except OSError as exc:
                block(
                    item.id,
                    item.old_path,
                    f"Cannot inspect destination folder: {exc}",
                )
                changed = True
                continue
            destination = path_key(item.new_path)
            if destination in existing and destination not in source_keys:
                block(
                    item.id,
                    item.old_path,
                    f"Destination already exists: {item.new_path}",
                )
                changed = True


def preflight(
    renames: list[RenameProposal],
    tags: list[TagProposal],
) -> tuple[list[RenameProposal], list[TagProposal], list[ApplyResult]]:
    blocked: dict[str, ApplyResult] = {}

    def block(item_id: str, path: str, message: str) -> None:
        blocked.setdefault(
            item_id,
            blocked_result(item_id, path, message),
        )

    _validate_sources(renames, tags, block)
    _validate_unique_sources(renames, tags, blocked, block)
    _validate_temporary_space(tags, blocked, block)
    _validate_destinations(renames, blocked, block)
    safe_renames = [item for item in renames if item.id not in blocked]
    safe_tags = [item for item in tags if item.id not in blocked]
    results = [
        blocked[item.id]
        for item in (*renames, *tags)
        if item.id in blocked
    ]
    return safe_renames, safe_tags, results


__all__ = [
    "blocked_result",
    "preflight",
    "selected_proposals",
]
