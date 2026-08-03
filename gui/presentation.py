"""Pure presentation models for the Tk review view."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from renamer.proposal_selection import requires_review
from renamer.review_models import ReviewPlan


def format_local_timestamp(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        local = timestamp.astimezone()
        return local.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    except (TypeError, ValueError):
        return value


def tag_display(values: dict[str, object]) -> str:
    return " / ".join(
        str(value)
        for value in (values.get("artist", ""), values.get("title", ""))
        if value
    )


@dataclass(frozen=True)
class DisplayRow:
    tree: str
    item_id: str
    action: str
    path: str
    current: str = ""
    proposed: str = ""
    confidence: str = ""
    is_change: bool = False


def _action_label(item, default: str) -> str:
    if getattr(item, "artwork_after", None) is not None:
        return "Review cover art" if requires_review(item) else "Cover art + metadata"
    return "Needs review" if requires_review(item) else default


def _tag_proposed_display(item) -> str:
    values = tag_display(item.after)
    if item.artwork_after is None:
        return values
    album = str(item.after.get("album") or "").strip()
    artwork = f"Embed cover art: {album}" if album else "Embed cover art"
    return " / ".join(value for value in (values, artwork) if value)


def plan_rows(plan: ReviewPlan) -> tuple[DisplayRow, ...]:
    rows = []
    for item in plan.rename_proposals:
        rows.append(
            DisplayRow(
                tree="renames",
                item_id=item.id,
                action=_action_label(item, "Rename"),
                path=item.old_path,
                current=str(item.current_values.get("filename", "")),
                proposed=str(item.proposed_values.get("filename", "")),
                confidence=(
                    "review" if requires_review(item) else item.confidence
                ),
                is_change=True,
            )
        )
    for item in plan.tag_proposals:
        rows.append(
            DisplayRow(
                tree="tags",
                item_id=item.id,
                action=_action_label(
                    item,
                    "Metadata enrichment"
                    if item.evidence.get("musicbrainz")
                    else "Tag repair",
                ),
                path=item.path,
                current=tag_display(item.before),
                proposed=_tag_proposed_display(item),
                confidence=(
                    "review" if requires_review(item) else item.confidence
                ),
                is_change=True,
            )
        )
    for item in plan.duplicate_findings:
        paths = item.paths or ("",)
        for index, path in enumerate(paths, start=1):
            rows.append(
                DisplayRow(
                    tree="duplicates",
                    item_id=f"{item.id}:{index}",
                    action=f"{item.classification} ({index}/{len(paths)})",
                    path=path,
                    current=item.recommendation,
                    confidence=item.confidence,
                )
            )
    for index, item in enumerate(plan.issues):
        rows.append(
            DisplayRow(
                tree="errors",
                item_id=f"issue-{index}",
                action=item.get("category", "error"),
                path=item.get("path", ""),
                current=item.get("message", ""),
                confidence="warning",
            )
        )
    return tuple(rows)


def filename_validation_error(
    filename: str,
    old_path: str,
) -> str | None:
    if not filename:
        return "Enter a filename."
    if filename in {".", ".."}:
        return "That is not a valid filename."
    if any(character in set('<>:"/\\|?*') for character in filename):
        return "The filename contains characters Windows does not allow."
    if filename.endswith((" ", ".")):
        return "A Windows filename cannot end with a space or period."
    if Path(filename).suffix.casefold() != Path(old_path).suffix.casefold():
        return "Keep the original file extension."
    return None


__all__ = [
    "DisplayRow",
    "filename_validation_error",
    "format_local_timestamp",
    "plan_rows",
    "tag_display",
]
