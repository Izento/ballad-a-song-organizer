"""Pure presentation models for the Tk review view."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gui.theme import _IMAGE_EXTENSIONS, _SHARED_ARTWORK_NAMES
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


def format_progress_log(stage: str, current: int, total: int, path: str) -> str:
    """Format one background-worker progress event for the activity pane."""
    return f"{stage}: {current}/{total}  {path or 'working'}"


def shared_folder_artwork(folder: str) -> tuple[Path, ...]:
    """Return player fallback artwork placed directly in one music folder."""
    candidates = []
    for path in Path(folder).iterdir():
        name = path.name.casefold()
        generated_album_art = (
            name.startswith("albumart_") and path.suffix.casefold() in _IMAGE_EXTENSIONS
        )
        if path.is_file() and (name in _SHARED_ARTWORK_NAMES or generated_album_art):
            candidates.append(path)
    return tuple(sorted(candidates, key=lambda path: path.name.casefold()))


def confidence_color(confidence: object) -> str:
    """Return the inspector color for a proposal confidence."""
    return {
        "HIGH": "green",
        "MEDIUM": "#b8860b",
        "LOW": "red",
        "BLOCKING": "red",
    }.get(str(confidence).upper(), "black")


def metadata_differences(proposal) -> tuple[tuple[str, object, object], ...]:
    """Return the compact metadata field comparison for the inspector."""
    if not (hasattr(proposal, "before") and hasattr(proposal, "after")):
        return ()
    before, after = proposal.before, proposal.after
    return (
        ("Artist", before.get("artist"), after.get("artist")),
        ("Title", before.get("title"), after.get("title")),
        ("Album", before.get("album"), after.get("album")),
        ("Track", before.get("track_number"), after.get("track_number")),
    )


def proposal_evidence(proposal) -> tuple[dict, dict]:
    """Normalize provider evidence from models and plain mappings."""
    evidence = getattr(proposal, "evidence", None)
    values = evidence.to_dict() if hasattr(evidence, "to_dict") else evidence
    values = values if isinstance(values, dict) else {}
    return values.get("identification") or {}, values.get("musicbrainz") or {}


def tag_display(values: dict[str, object]) -> str:
    return " / ".join(
        str(value) for value in (values.get("artist", ""), values.get("title", "")) if value
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
    rows.extend(_rename_rows(plan))
    rows.extend(_tag_rows(plan))
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


def _rename_rows(plan: ReviewPlan) -> tuple[DisplayRow, ...]:
    return tuple(
        DisplayRow(
            tree="renames",
            item_id=item.id,
            action=_action_label(item, "Rename"),
            path=item.old_path,
            current=str(item.current_values.get("filename", "")),
            proposed=str(item.proposed_values.get("filename", "")),
            confidence="review" if requires_review(item) else item.confidence,
            is_change=True,
        )
        for item in plan.rename_proposals
    )


def _tag_rows(plan: ReviewPlan) -> tuple[DisplayRow, ...]:
    return tuple(
        DisplayRow(
            tree="tags",
            item_id=item.id,
            action=_action_label(
                item,
                "Metadata enrichment" if item.evidence.get("musicbrainz") else "Tag repair",
            ),
            path=item.path,
            current=tag_display(item.before),
            proposed=_tag_proposed_display(item),
            confidence="review" if requires_review(item) else item.confidence,
            is_change=True,
        )
        for item in plan.tag_proposals
    )


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
    "confidence_color",
    "filename_validation_error",
    "format_progress_log",
    "format_local_timestamp",
    "metadata_differences",
    "plan_rows",
    "proposal_evidence",
    "shared_folder_artwork",
    "tag_display",
]
