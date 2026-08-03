"""Review-plan Treeview construction and row rendering."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from gui.presentation import plan_rows
from gui.theme import (
    _CONFIDENCE_ROW_STYLES,
    _FIXED_TREE_COLUMNS,
    _TREE_STYLE,
    _confidence_row_tags,
)
from renamer.review_models import ReviewPlan


class TreeMixin:
    """Render and maintain the plan's review-table rows."""

    def _tree_spec(self, key: str) -> tuple[tuple[str, ...], dict, dict]:
        if key == "renames":
            return self._rename_tree_spec()
        if key == "tags":
            return self._tag_tree_spec()
        return self._readonly_tree_spec()

    def _rename_tree_spec(self) -> tuple[tuple[str, ...], dict, dict]:
        columns = ("selected", "action", "current", "proposed", "confidence")
        headings = {
            "selected": "", "action": "Action", "current": "Current filename",
            "proposed": "Proposed filename", "confidence": "Confidence",
        }
        widths = {
            "selected": 26, "action": 82, "current": 440, "proposed": 440,
            "confidence": 72,
        }
        return columns, headings, widths

    def _tag_tree_spec(self) -> tuple[tuple[str, ...], dict, dict]:
        columns = ("selected", "action", "file", "current", "proposed", "confidence")
        headings = {
            "selected": "", "action": "Action", "file": "File",
            "current": "Current tags", "proposed": "Proposed tags",
            "confidence": "Confidence",
        }
        widths = {
            "selected": 26, "action": 82, "file": 170, "current": 350,
            "proposed": 350, "confidence": 72,
        }
        return columns, headings, widths

    def _readonly_tree_spec(self) -> tuple[tuple[str, ...], dict, dict]:
        columns = ("action", "file", "details", "confidence")
        headings = {
            "action": "Action", "file": "File", "details": "Details",
            "confidence": "Confidence",
        }
        widths = {"action": 140, "file": 350, "details": 420, "confidence": 72}
        return columns, headings, widths

    def _make_tree(self, parent: ttk.Frame, key: str) -> ttk.Treeview:
        columns, headings, widths = self._tree_spec(key)
        frame, tree = self._create_tree_widget(parent, columns, key)
        self._tree_headings[key] = dict(headings)
        self._configure_tree_columns(tree, key, columns, headings, widths)
        self._configure_tree_tags(tree)
        self._add_tree_scrollbars(frame, tree)
        self._bind_tree_events(tree, key)
        return tree

    def _create_tree_widget(
        self, parent: ttk.Frame, columns: tuple[str, ...], key: str
    ) -> tuple[ttk.Frame, ttk.Treeview]:
        ttk.Style(parent).configure(_TREE_STYLE, rowheight=24)
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            selectmode="extended" if key in {"renames", "tags"} else "browse",
            style=_TREE_STYLE,
        )
        return frame, tree

    def _configure_tree_columns(
        self, tree, key: str, columns: tuple[str, ...], headings: dict, widths: dict
    ) -> None:
        for column in columns:
            tree.heading(
                column,
                text=headings[column],
                command=lambda name=key, value=column: self._sort_tree_column(
                    name, value
                ),
            )
            fixed = column in _FIXED_TREE_COLUMNS
            tree.column(
                column, width=widths[column],
                minwidth=widths[column] if fixed else 180, stretch=not fixed,
                anchor=tk.CENTER if column in {"selected", "confidence"} else tk.W,
            )

    def _configure_tree_tags(self, tree) -> None:
        for level, (background, foreground) in _CONFIDENCE_ROW_STYLES.items():
            tree.tag_configure(
                f"conf-{level}", background=background, foreground=foreground
            )

    def _add_tree_scrollbars(self, frame: ttk.Frame, tree) -> None:
        vertical = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vertical.grid(row=0, column=1, sticky=tk.NS)
        horizontal.grid(row=1, column=0, sticky=tk.EW)

    def _bind_tree_events(self, tree, key: str) -> None:
        tree.bind("<Button-1>", lambda event, name=key: self._handle_tree_click(name, event))
        tree.bind(
            "<<TreeviewSelect>>", lambda event, name=key: self._on_tree_select(name, event)
        )
        tree.bind(
            "<Double-Button-1>",
            lambda event, name=key: self._handle_tree_double_click(name, event),
        )
        tree.bind(
            "<Button-3>",
            lambda event, name=key: self._handle_tree_context_menu(name, event),
        )

    def _clear_trees(self) -> None:
        headings = getattr(self, "_tree_headings", {})
        for tree_name, tree in self.trees.items():
            tree.delete(*tree.get_children())
            for column, label in headings.get(tree_name, {}).items():
                tree.heading(column, text=label)
        self.session.row_ids.clear()
        self.session.row_paths.clear()
        self.session.sort_state.clear()
        self.session.selection_anchors.clear()

    def _sort_tree_column(self, tree_name: str, column: str) -> None:
        """Sort rows with ``move`` so row-to-proposal maps remain valid."""
        tree = self.trees[tree_name]
        reverse = self.session.sort_state.get((tree_name, column), False)
        children = sorted(
            tree.get_children(""), key=lambda iid: tree.set(iid, column).casefold(),
            reverse=reverse,
        )
        for index, iid in enumerate(children):
            tree.move(iid, "", index)
        self.session.sort_state[(tree_name, column)] = not reverse
        self._set_sort_heading(tree, tree_name, column, reverse)

    def _set_sort_heading(self, tree, tree_name: str, column: str, reverse: bool) -> None:
        arrow = "▼" if reverse else "▲"
        for other_column, label in self._tree_headings.get(tree_name, {}).items():
            tree.heading(
                other_column, text=f"{label} {arrow}" if other_column == column else label
            )

    def _populate_plan(self, plan: ReviewPlan) -> None:
        self._clear_trees()
        for row in plan_rows(plan):
            if row.is_change:
                self._insert_change_row(
                    row.tree, row.item_id, row.action, row.path, row.current,
                    row.proposed, row.confidence,
                )
            else:
                self._insert_row(
                    row.tree, row.item_id, row.action, row.path, row.current,
                    row.confidence,
                )

    def _insert_row(
        self, tree_name: str, item_id: str, action: str, path: str,
        summary: str, confidence: str,
    ) -> None:
        tree = self.trees[tree_name]
        row = tree.insert(
            "", tk.END,
            values=(action, Path(path).name if path else "", summary, confidence),
            tags=_confidence_row_tags(confidence),
        )
        self.session.row_ids[(tree_name, row)] = item_id
        self.session.row_paths[(tree_name, row)] = path

    def _insert_change_row(
        self, tree_name: str, item_id: str, action: str, path: str,
        current: str, proposed: str, confidence: str,
    ) -> None:
        tree = self.trees[tree_name]
        values = ["☐", action]
        if tree_name == "tags":
            values.append(Path(path).name if path else "")
        values.extend((current, proposed, confidence))
        row = tree.insert("", tk.END, values=values, tags=_confidence_row_tags(confidence))
        self.session.row_ids[(tree_name, row)] = item_id
        self.session.row_paths[(tree_name, row)] = path


__all__ = ["TreeMixin"]
