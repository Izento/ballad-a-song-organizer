"""Selection, checkbox, and primary-action policy."""

from __future__ import annotations

import tkinter as tk

from gui.theme import _FIXED_TREE_COLUMNS, _SHIFT_MASK
from renamer.proposal_selection import (
    action_items,
    artwork_ids,
    expand_group_selection,
    grouped_action_ids,
    ready_ids,
    recommended_ids,
)


class SelectionControllerMixin:
    """Coordinate song-group and component-level review choices."""

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
        if tree.identify_column(event.x) == "#1" and tree_name in {"changes", "duplicates"}:
            return "break"
        path = self.session.row_paths.get((tree_name, tree.identify_row(event.y)))
        if path:
            self._open_with_default_app(path)
        return None

    def _handle_tree_click(self, tree_name: str, event):
        tree = self.trees[tree_name]
        if self._blocks_fixed_column_resize(tree, event):
            return "break"
        if tree_name not in {"changes", "duplicates"}:
            return None
        return self._handle_selectable_tree_click(tree_name, tree, event)

    def _handle_selectable_tree_click(self, tree_name: str, tree, event):
        column, row = tree.identify_column(event.x), tree.identify_row(event.y)
        if not row:
            return None if column != "#1" else "break"
        if column != "#1":
            self._remember_selection_anchor(tree_name, row, event)
            return None
        if tree_name == "changes":
            return self._toggle_change_rows(tree, row, event)
        return self._toggle_duplicate_rows(tree, row, event)

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

    def _toggle_change_rows(self, tree, row: str, event):
        rows = self._checkbox_rows("changes", tree, row, event)
        group_ids = {
            group_id
            for selected_row in rows
            if (group_id := self.session.row_group_ids.get(("changes", selected_row)))
        }
        clicked_group = self.session.row_group_ids.get(("changes", row))
        if not clicked_group or not group_ids:
            return "break"
        eligible_ids = self._eligible_group_ids(group_ids)
        clicked_ids = self._eligible_group_ids({clicked_group})
        if not clicked_ids:
            self.status_var.set("This song has no selectable changes.")
            return "break"
        selected = set(self.session.selected_ids)
        if clicked_ids <= selected:
            selected -= eligible_ids
        else:
            selected |= eligible_ids
        self._set_selected_ids(selected, expand_groups=False)
        return "break"

    def _toggle_duplicate_rows(self, tree, row: str, event):
        rows = self._checkbox_rows("duplicates", tree, row, event)
        targets = {
            self.session.duplicate_row_ids[("duplicates", selected_row)]
            for selected_row in rows
            if ("duplicates", selected_row) in self.session.duplicate_row_ids
        }
        clicked = self.session.duplicate_row_ids.get(("duplicates", row))
        if not clicked or not targets:
            return "break"
        selected = {
            finding_id: set(paths)
            for finding_id, paths in self.session.duplicate_selected_paths.items()
        }
        clicked_selected = clicked[1] in selected.get(clicked[0], set())
        if clicked_selected:
            self._remove_duplicate_targets(selected, targets)
        elif self._can_select_duplicate_targets(selected, targets):
            self._add_duplicate_targets(selected, targets)
        else:
            self.status_var.set("Keep at least one file in each duplicate group.")
        self.session.duplicate_selected_paths = {
            finding_id: paths for finding_id, paths in selected.items() if paths
        }
        self._render_duplicate_checkboxes()
        return "break"

    def _eligible_group_ids(self, group_ids: set[str]) -> set[str]:
        plan = self.session.plan
        if plan is None:
            return set()
        return {
            item.id
            for item in action_items(plan)
            if item.decision_group_id in group_ids
            and item.apply_eligible
            and not self._group_was_applied(item)
        }

    def _remove_duplicate_targets(self, selected: dict[str, set[str]], targets) -> None:
        for finding_id, path in targets:
            selected.setdefault(finding_id, set()).discard(path)

    def _add_duplicate_targets(self, selected: dict[str, set[str]], targets) -> None:
        for finding_id, path in targets:
            selected.setdefault(finding_id, set()).add(path)

    def _can_select_duplicate_targets(self, selected: dict[str, set[str]], targets) -> bool:
        plan = self.session.plan
        if plan is None:
            return False
        proposed = {key: set(value) for key, value in selected.items()}
        self._add_duplicate_targets(proposed, targets)
        findings = {item.id: item for item in plan.duplicate_findings}
        return all(
            set(finding.paths) - proposed.get(finding_id, set())
            for finding_id, finding in findings.items()
            if finding_id in proposed
        )

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

    def _active_action_scope(self) -> tuple[str, tuple] | None:
        plan = self.session.plan
        if plan is None:
            return None
        active_tab = str(self.notebook.select())
        if active_tab == str(self.tabs["changes"]):
            return "changes", action_items(plan)
        self.status_var.set("Open Planned changes before selecting changes.")
        return None

    def _select_recommended(self) -> None:
        plan = self.session.plan
        if plan is None:
            return
        selected = self.session.selected_ids | recommended_ids(plan)
        self._set_selected_ids(selected, expand_groups=False)
        self.status_var.set(
            f"Added {len(recommended_ids(plan))} recommended change(s) to the selection."
        )

    def _select_all(self) -> None:
        plan = self.session.plan
        if plan is None:
            return
        selected = self.session.selected_ids | ready_ids(plan)
        self._set_selected_ids(selected, expand_groups=False)
        skipped = len(action_items(plan)) - len(ready_ids(plan))
        self.status_var.set(
            f"Added {len(ready_ids(plan))} ready change(s); {skipped} need review or are blocked."
        )

    def _select_artwork(self) -> None:
        plan = self.session.plan
        if plan is None:
            return
        artwork = artwork_ids(plan)
        self._set_selected_ids(self.session.selected_ids | artwork, expand_groups=False)
        selected = len(self.session.selected_ids & artwork)
        skipped = sum(
            item.artwork_after is not None and not item.apply_eligible
            for item in plan.tag_proposals
        )
        self.notebook.select(self.tabs["changes"])
        self.status_var.set(
            f"Selected {selected} verified cover-art change(s)"
            + (f"; {skipped} blocking item(s) skipped." if skipped else ".")
        )

    def _clear_selection(self) -> None:
        self._set_selected_ids(set(), expand_groups=False)
        self.status_var.set("Selection cleared.")

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

    def _toggle_component_selection(self, proposal_id: str, checked: bool) -> None:
        proposal = self._proposal_for_id(proposal_id)
        if proposal is None or self._group_was_applied(proposal):
            return
        selected = set(self.session.selected_ids)
        if checked and proposal.apply_eligible:
            selected.add(proposal_id)
        else:
            selected.discard(proposal_id)
        self._set_selected_ids(selected, expand_groups=False)

    def _without_applied_ids(self, selected: set[str], plan) -> set[str]:
        return {
            item_id
            for group_id, item_ids in grouped_action_ids(plan).items()
            if group_id not in self.session.applied_group_ids
            for item_id in item_ids
            if item_id in selected
        }

    def _render_selected_checkboxes(self) -> None:
        tree = self.trees.get("changes")
        if tree is not None:
            for row in tree.get_children(""):
                values = list(tree.item(row, "values"))
                if values:
                    group_id = self.session.row_group_ids.get(("changes", row), "")
                    values[0] = self._group_checkbox_state(group_id)
                    tree.item(row, values=values)
        self._render_duplicate_checkboxes()

    def _group_checkbox_state(self, group_id: str) -> str:
        eligible = self._eligible_group_ids({group_id})
        selected = self.session.selected_ids & eligible
        if not selected:
            return "☐"
        if selected == eligible:
            return "☑"
        return "◩"

    def _render_duplicate_checkboxes(self) -> None:
        tree = self.trees.get("duplicates")
        if tree is None:
            return
        selected = self.session.duplicate_selected_paths
        for row in tree.get_children(""):
            values = list(tree.item(row, "values"))
            target = self.session.duplicate_row_ids.get(("duplicates", row))
            if values and target:
                finding_id, path = target
                values[0] = "☑" if path in selected.get(finding_id, set()) else "☐"
                tree.item(row, values=values)
        self._update_duplicate_remove_button()

    def _update_duplicate_remove_button(self) -> None:
        button = getattr(self, "remove_duplicates_button", None)
        if button is None:
            return
        has_selection = any(self.session.duplicate_selected_paths.values())
        busy = getattr(self.jobs, "active", False)
        button.configure(state=tk.NORMAL if has_selection and not busy else tk.DISABLED)


__all__ = ["SelectionControllerMixin"]
