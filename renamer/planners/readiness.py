"""Rename readiness and coordinated tag-planning policy."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..domain.issues import IssueCode, ReviewIssue
from ..filename_builder import split_feat
from ..filename_parser import normalize_text, split_feature_names
from ..review_models import (
    RenameProposal,
    TagProposal,
    canonical_path,
    path_key,
    proposal_id,
)
from ..tag_audit import expected_tags_from_filename

_READINESS_ISSUE_CODES = {
    IssueCode.DESTINATION_COLLISION,
    IssueCode.DESTINATION_EXISTS,
}


def proposal_identity(
    values: dict[str, str],
) -> tuple[str, str, tuple[str, ...]]:
    title, title_features = split_feat(values.get("title", ""))
    features = list(title_features)
    for key in ("contributors", "subtitle"):
        features.extend(split_feature_names(values.get(key, "")))
    return (
        normalize_text(values.get("artist", "")),
        normalize_text(title),
        tuple(sorted(normalize_text(feature) for feature in features)),
    )


def _with_warning(item: RenameProposal, message: str) -> RenameProposal:
    if message in item.warnings:
        return item
    issue = ReviewIssue.from_message(message)
    return replace(
        item,
        warnings=(*item.warnings, message),
        review_issues=(*item.review_issues, issue),
    )


def _without_readiness_warnings(item: RenameProposal) -> RenameProposal:
    review_issues = tuple(
        issue
        for issue in item.review_issues
        if issue.code not in _READINESS_ISSUE_CODES
    )
    warnings = tuple(issue.message for issue in review_issues)
    if warnings == item.warnings:
        return item
    return replace(item, warnings=warnings, review_issues=review_issues)


def _coordinated_tag_proposal(
    rename: RenameProposal,
    existing: TagProposal | None,
) -> TagProposal | None:
    snapshot = existing.snapshot if existing is not None else rename.snapshot
    current = dict(existing.before if existing is not None else snapshot.tags)
    expected, _ = expected_tags_from_filename(rename.new_path, current)
    relevant = sorted(set(current) | set(expected))
    before = {key: current.get(key, "") for key in relevant}
    after = {key: expected.get(key, "") for key in relevant}
    if before == after:
        return None
    return TagProposal(
        id=proposal_id(
            "tag",
            rename.old_path,
            {"before": before, "after": after},
        ),
        decision_group_id=rename.decision_group_id,
        snapshot=snapshot,
        path=rename.old_path,
        before=before,
        after=after,
        confidence=rename.confidence,
        reason="Sync tags to the proposed filename.",
        warnings=rename.warnings,
    )


def coordinate_tag_proposals(
    rename_proposals: list[RenameProposal],
    tag_proposals: list[TagProposal],
) -> tuple[list[TagProposal], list[ReviewIssue], set[str]]:
    """Align tags with each rename's reviewed final filename."""
    existing_by_group = {
        item.decision_group_id: item for item in tag_proposals
    }
    coordinated: list[TagProposal] = []
    issues: list[ReviewIssue] = []
    renamed_groups: set[str] = set()
    synchronized_paths: set[str] = set()
    for rename in rename_proposals:
        renamed_groups.add(rename.decision_group_id)
        synchronized_paths.add(path_key(rename.old_path))
        try:
            proposal = _coordinated_tag_proposal(
                rename,
                existing_by_group.get(rename.decision_group_id),
            )
        except ValueError as exc:
            issues.append(
                ReviewIssue.from_dict(
                    {
                        "path": canonical_path(rename.old_path),
                        "category": "tag-sync",
                        "message": (
                            f"Tags were not prepared for this rename: {exc}"
                        ),
                    }
                )
            )
            continue
        if proposal is not None:
            coordinated.append(proposal)
    coordinated.extend(
        item
        for item in tag_proposals
        if item.decision_group_id not in renamed_groups
    )
    return coordinated, issues, synchronized_paths


def refresh_rename_readiness(
    proposals: list[RenameProposal] | tuple[RenameProposal, ...],
) -> list[RenameProposal]:
    """Mark destination conflicts that are known during review."""
    updated = [_without_readiness_warnings(item) for item in proposals]
    destinations: dict[str, list[int]] = {}
    for index, item in enumerate(updated):
        destinations.setdefault(path_key(item.new_path), []).append(index)
    for indexes in destinations.values():
        if len(indexes) > 1:
            for index in indexes:
                updated[index] = _with_warning(
                    updated[index],
                    "Destination collides with another proposal.",
                )
    source_keys = {path_key(item.old_path) for item in updated}
    for index, item in enumerate(updated):
        if (
            Path(item.new_path).exists()
            and path_key(item.new_path) not in source_keys
        ):
            updated[index] = _with_warning(
                item,
                f"Destination already exists: {item.new_path}",
            )
    return updated


__all__ = [
    "coordinate_tag_proposals",
    "proposal_identity",
    "refresh_rename_readiness",
]
