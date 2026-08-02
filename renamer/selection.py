"""Shared proposal eligibility and decision-group selection policy."""

from __future__ import annotations

from collections.abc import Iterable

from .review_models import ReviewPlan


def action_items(plan: ReviewPlan):
    return (*plan.rename_proposals, *plan.tag_proposals)


def requires_review(item) -> bool:
    return item.requires_review


def is_high_confidence_action(item) -> bool:
    return (
        item.confidence == "high"
        and item.apply_eligible
        and not item.requires_review
    )


def grouped_action_ids(plan: ReviewPlan) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for item in action_items(plan):
        groups.setdefault(item.decision_group_id, set()).add(item.id)
    return groups


def ready_ids(plan: ReviewPlan) -> set[str]:
    items_by_group: dict[str, list] = {}
    for item in action_items(plan):
        items_by_group.setdefault(item.decision_group_id, []).append(item)
    return {
        item.id
        for items in items_by_group.values()
        if all(
            item.apply_eligible and not item.requires_review
            for item in items
        )
        for item in items
    }


def expand_group_selection(
    plan: ReviewPlan,
    selected_ids: Iterable[str],
    *,
    include_review: bool = False,
) -> set[str]:
    """Expand selected proposals to coordinated, applyable group members.

    Automatic selection keeps review items out by default. The GUI passes
    ``include_review=True`` for an explicit checkbox click so a user can
    approve an applyable warning after inspecting it.
    """
    groups = grouped_action_ids(plan)
    selected = set(selected_ids)
    selected_groups = {
        group_id
        for group_id, item_ids in groups.items()
        if selected & item_ids
    }
    allowed_ids = (
        {
            item.id
            for item in action_items(plan)
            if item.apply_eligible
        }
        if include_review
        else ready_ids(plan)
    )
    return {
        item_id
        for group_id in selected_groups
        for item_id in groups[group_id]
    } & allowed_ids


def recommended_ids(plan: ReviewPlan) -> set[str]:
    items_by_group: dict[str, list] = {}
    for item in action_items(plan):
        items_by_group.setdefault(item.decision_group_id, []).append(item)
    return {
        item.id
        for items in items_by_group.values()
        if all(is_high_confidence_action(item) for item in items)
        for item in items
    }


def artwork_ids(plan: ReviewPlan) -> set[str]:
    """Return applyable tag proposals that embed verified missing artwork."""
    return {
        item.id
        for item in plan.tag_proposals
        if item.artwork_after is not None and item.apply_eligible
    }


def eligible_ids(items: Iterable, *, include_review: bool = False) -> list[str]:
    return [
        item.id
        for item in items
        if item.apply_eligible
        and (include_review or not item.requires_review)
    ]


__all__ = [
    "action_items",
    "artwork_ids",
    "eligible_ids",
    "expand_group_selection",
    "grouped_action_ids",
    "is_high_confidence_action",
    "ready_ids",
    "recommended_ids",
    "requires_review",
]
