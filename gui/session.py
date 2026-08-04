"""Mutable review state owned by one desktop window."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from renamer.proposal_selection import action_items, grouped_action_ids
from renamer.review_models import ReviewPlan


@dataclass
class ReviewSession:
    """Keep review data separate from Tk widgets and commands."""

    plan: ReviewPlan | None = None
    selected_ids: set[str] = field(default_factory=set)
    applied_group_ids: set[str] = field(default_factory=set)
    recovery_overrides: set[str] = field(default_factory=set)
    row_ids: dict[tuple[str, str], str] = field(default_factory=dict)
    row_group_ids: dict[tuple[str, str], str] = field(default_factory=dict)
    row_paths: dict[tuple[str, str], str] = field(default_factory=dict)
    duplicate_row_ids: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)
    duplicate_selected_paths: dict[str, set[str]] = field(default_factory=dict)
    selection_anchors: dict[str, str] = field(default_factory=dict)
    sort_state: dict[tuple[str, str], bool] = field(default_factory=dict)
    last_run_checked_duplicates: bool = True

    def proposal_for_id(self, item_id: str) -> Any | None:
        """Return one actionable proposal by its stable identifier."""
        if self.plan is None:
            return None
        return next(
            (item for item in action_items(self.plan) if item.id == item_id),
            None,
        )

    def group_was_applied(self, proposal: Any) -> bool:
        """Return whether this proposal's song changed in this run."""
        return proposal.decision_group_id in self.applied_group_ids

    def proposals_for_group(self, group_id: str) -> tuple[Any, ...]:
        """Return all filename and metadata proposals for one song."""
        if self.plan is None:
            return ()
        return tuple(item for item in action_items(self.plan) if item.decision_group_id == group_id)

    def selection_group_count(self) -> int:
        """Return selected decision groups rather than individual actions."""
        if self.plan is None:
            return len(self.selected_ids)
        groups = grouped_action_ids(self.plan)
        return sum(bool(self.selected_ids & item_ids) for item_ids in groups.values())

    def record_applied_groups(self, results: Any) -> None:
        """Remember successful groups and remove their selected action IDs."""
        if self.plan is None:
            return
        successful_ids = {result.proposal_id for result in results if result.status == "succeeded"}
        self.applied_group_ids.update(
            item.decision_group_id for item in action_items(self.plan) if item.id in successful_ids
        )
        self.selected_ids.difference_update(successful_ids)


__all__ = ["ReviewSession"]
