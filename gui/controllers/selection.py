"""Selection, checkbox, and primary-action policy."""

from __future__ import annotations

from gui.theme import _FIXED_TREE_COLUMNS, _SHIFT_MASK
from renamer.proposal_selection import (
    artwork_ids,
    expand_group_selection,
    grouped_action_ids,
    is_high_confidence_action,
)


class SelectionControllerMixin:
    """Coordinate review-table choices across linked tag and rename rows."""

    def _proposal_for_id(self, item_id: str):
        return self.session.proposal_for_id(item_id)

    def _record_applied_groups(self, results) -> None:
        self.session.record_applied_groups(results)
        self._set_selected_ids(self.session.selected_ids)

    def _group_was_applied(self, proposal) -> bool:
        return self.session.group_was_applied(proposal)

    def _selection_group_count(self) -> int:
        return self.session.selection_group_count()

    def _handle_tree_double_click(self, tree_name: str, event):
        tree = self.trees[tree_name]
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        if tree.identify_column(event.x) == "#1" and tree_name in {"renames", "tags"}:
            return "break"
        path = self.session.row_paths.get((tree_name, tree.identify_row(event.y)))
        if path:
            self._open_with_default_app(path)
        return None

    def _handle_tree_click(self, tree_name: str, event):
        tree = self.trees[tree_name]
        if self._blocks_fixed_column_resize(tree, event):
            return "break"
        if tree_name not in {"renames", "tags"}:
            return None
        column, row = tree.identify_column(event.x), tree.identify_row(event.y)
        if not row:
            return None if column != "#1" else "break"
        if column != "#1":
            self._remember_selection_anchor(tree_name, row, event)
            return None
        return self._toggle_checked_rows(tree_name, tree, row, event)

    def _blocks_fixed_column_resize(self, tree, event) -> bool:
        if tree.identify_region(event.x, event.y) != "separator":
            return False
        column = tree.identify_column(event.x)
        index = int(column[1:]) - 1 if column.startswith("#") else -1
        adjacent = {column, f"#{index}"} if index > 0 else {column}
        columns = tree["columns"]
        return any(
            columns[int(name[1:]) - 1] in _FIXED_TREE_COLUMNS
            for name in adjacent
            if name.startswith("#") and int(name[1:]) <= len(columns)
        )

    def _remember_selection_anchor(self, tree_name: str, row: str, event) -> None:
        shift_pressed = bool(getattr(event, "state", 0) & _SHIFT_MASK)
        if not shift_pressed or tree_name not in self.session.selection_anchors:
            self.session.selection_anchors[tree_name] = row

    def _toggle_checked_rows(self, tree_name: str, tree, row: str, event):
        rows = self._checkbox_rows(tree_name, tree, row, event)
        item_ids = {
            item_id
            for selected_row in rows
            if (item_id := self.session.row_ids.get((tree_name, selected_row)))
        }
        clicked_id = self.session.row_ids.get((tree_name, row))
        if not clicked_id or not item_ids:
            return "break"
        clicked = self._proposal_for_id(clicked_id)
        if clicked is None:
            self._toggle_unbound_ids(clicked_id, item_ids)
            return "break"
        if self._selection_is_blocked(clicked):
            return "break"
        self._toggle_proposal_groups(clicked_id, item_ids)
        return "break"

    def _checkbox_rows(self, tree_name: str, tree, row: str, event) -> list[str]:
        rows = list(tree.selection())
        if getattr(event, "state", 0) & _SHIFT_MASK:
            rows = self._shift_range(tree_name, tree, row, rows)
        else:
            self.session.selection_anchors[tree_name] = row
        if row not in rows:
            tree.selection_set(row)
            return [row]
        return rows

    def _shift_range(self, tree_name: str, tree, row: str, rows: list[str]) -> list[str]:
        anchor = self.session.selection_anchors.get(tree_name)
        visible_rows = list(tree.get_children(""))
        if anchor in visible_rows and row in visible_rows:
            start, end = visible_rows.index(anchor), visible_rows.index(row)
            rows = visible_rows[min(start, end) : max(start, end) + 1]
            tree.selection_set(rows)
        return rows

    def _toggle_unbound_ids(self, clicked_id: str, item_ids: set[str]) -> None:
        selected = self.session.selected_ids
        self._set_selected_ids(
            selected - item_ids if clicked_id in selected else selected | item_ids
        )

    def _selection_is_blocked(self, proposal) -> bool:
        if self._group_was_applied(proposal):
            self.status_var.set(
                "This song was already changed in this run. "
                "Organize again before making more changes."
            )
            return True
        if not proposal.apply_eligible:
            self.status_var.set(
                "This song has a blocking issue and cannot be selected until it is resolved."
            )
            return True
        return False

    def _toggle_proposal_groups(self, clicked_id: str, item_ids: set[str]) -> None:
        plan = self.session.plan
        groups = grouped_action_ids(plan)
        selected_groups = {
            proposal.decision_group_id
            for item_id in item_ids
            if (proposal := self._proposal_for_id(item_id)) is not None
            and proposal.apply_eligible
            and not self._group_was_applied(proposal)
        }
        grouped_ids = {item_id for group_id in selected_groups for item_id in groups[group_id]}
        selected = self.session.selected_ids
        self._set_selected_ids(
            selected - grouped_ids if clicked_id in selected else selected | grouped_ids
        )

    def _active_action_scope(self) -> tuple[str, tuple] | None:
        plan = self.session.plan
        if plan is None:
            return None
        active_tab = str(self.notebook.select())
        if active_tab == str(self.tabs["renames"]):
            return "filename", tuple(plan.rename_proposals)
        if active_tab == str(self.tabs["tags"]):
            return "metadata", tuple(plan.tag_proposals)
        self.status_var.set("Open Filename changes or Metadata changes before selecting changes.")
        return None

    def _select_recommended(self) -> None:
        scope = self._active_action_scope()
        if scope is None:
            return
        name, items = scope
        selected = {item.id for item in items if is_high_confidence_action(item)}
        self._set_selected_ids(selected, expand_groups=False)
        self.status_var.set(f"Selected {len(selected)} recommended {name} change(s).")

    def _select_all(self) -> None:
        scope = self._active_action_scope()
        if scope is None:
            return
        name, items = scope
        selected = {item.id for item in items if item.apply_eligible and not item.requires_review}
        self._set_selected_ids(selected, expand_groups=False)
        self.status_var.set(
            f"Selected {len(selected)} ready {name} change(s); "
            f"{len(items) - len(selected)} need review or are blocked."
        )

    def _select_artwork(self) -> None:
        plan = self.session.plan
        if plan is None:
            return
        artwork = artwork_ids(plan)
        self._set_selected_ids(artwork, expand_groups=False)
        selected = len(self.session.selected_ids & artwork)
        skipped = sum(
            item.artwork_after is not None and not item.apply_eligible
            for item in plan.tag_proposals
        )
        self.notebook.select(self.tabs["tags"])
        self.status_var.set(
            f"Selected {selected} verified cover-art change(s)"
            + (f"; {skipped} blocking item(s) skipped." if skipped else ".")
        )

    def _set_selected_ids(self, selected_ids, *, expand_groups: bool = True) -> None:
        plan = self.session.plan
        selected = (
            expand_group_selection(plan, selected_ids, include_review=True)
            if plan is not None and expand_groups
            else set(selected_ids)
        )
        if plan is not None:
            selected = self._without_applied_ids(selected, plan)
        self.session.selected_ids = selected
        self._render_selected_checkboxes()
        self._update_primary_button()

    def _without_applied_ids(self, selected: set[str], plan) -> set[str]:
        return {
            item_id
            for group_id, item_ids in grouped_action_ids(plan).items()
            if group_id not in self.session.applied_group_ids
            for item_id in item_ids
            if item_id in selected
        }

    def _render_selected_checkboxes(self) -> None:
        for tree_name in ("renames", "tags"):
            tree = self.trees[tree_name]
            for row in tree.get_children(""):
                values = list(tree.item(row, "values"))
                if values:
                    values[0] = (
                        "☑"
                        if self.session.row_ids.get((tree_name, row)) in self.session.selected_ids
                        else "☐"
                    )
                    tree.item(row, values=values)


__all__ = ["SelectionControllerMixin"]
