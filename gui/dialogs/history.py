"""Journal history and selective restoration dialog."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from gui.presentation import format_local_timestamp
from renamer.apply import batch_history, undo_batch
from renamer.review_models import path_key


class HistoryDialogMixin:
    """Browse durable apply history and restore whole or selected groups."""

    def _show_history(self) -> None:
        window, batch_tree, action_tree = self._build_history_window()
        batches = {item.get("batch_id", ""): item for item in batch_history()}
        action_groups: dict[str, str] = {}
        self._populate_batch_tree(batch_tree, batches)
        batch_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._load_batch_actions(
                batch_tree, action_tree, batches, action_groups
            ),
        )
        self._add_history_buttons(window, batch_tree, action_tree, action_groups)

    def _build_history_window(self):
        window = tk.Toplevel(self.root)
        window.title("Ballad history & restoration")
        window.geometry("880x460")
        paned = ttk.PanedWindow(window, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        batch_tree = self._history_tree(
            paned,
            "Applied Batches",
            ("status", "date", "root"),
            (("status", "Status", 90), ("date", "Date", 130), ("root", "Folder", 180)),
            1,
        )
        action_tree = self._history_tree(
            paned,
            "Changed Files in Batch",
            ("file", "action", "status"),
            (("file", "File / Target", 280), ("action", "Kind", 70), ("status", "Status", 80)),
            2,
        )
        return window, batch_tree, action_tree

    def _history_tree(self, paned, title: str, columns: tuple[str, ...], spec, weight: int):
        frame = ttk.Labelframe(paned, text=title, padding=6)
        paned.add(frame, weight=weight)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended")
        for column, label, width in spec:
            tree.heading(column, text=label)
            tree.column(column, width=width)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        return tree

    def _populate_batch_tree(self, tree, batches: dict[str, dict]) -> None:
        for batch_id, batch in batches.items():
            if batch_id:
                tree.insert(
                    "",
                    tk.END,
                    iid=batch_id,
                    values=(
                        batch.get("status", "unknown"),
                        format_local_timestamp(batch.get("created_at", "")),
                        batch.get("root", ""),
                    ),
                )

    def _load_batch_actions(self, batch_tree, action_tree, batches, action_groups) -> None:
        action_tree.delete(*action_tree.get_children())
        action_groups.clear()
        selected = batch_tree.selection()
        if not selected or not (batch := batches.get(selected[0])):
            return
        for index, action in enumerate(batch.get("actions", [])):
            path = action.get("path") or action.get("new") or action.get("old") or "File"
            row_id = f"act_{index}"
            action_groups[row_id] = action.get("decision_group_id") or path_key(path)
            action_tree.insert(
                "",
                tk.END,
                iid=row_id,
                values=(Path(path).name, action.get("kind", ""), action.get("status", "")),
            )

    def _add_history_buttons(self, window, batch_tree, action_tree, action_groups) -> None:
        bottom = ttk.Frame(window, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        ttk.Button(
            bottom,
            text="Restore selected file(s)",
            command=lambda: self._restore_selected_history(
                window, batch_tree, action_tree, action_groups
            ),
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            bottom,
            text="Restore entire batch",
            command=lambda: self._restore_entire_history(window, batch_tree),
        ).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Close", command=window.destroy).pack(side=tk.RIGHT)

    def _restore_selected_history(self, window, batch_tree, action_tree, action_groups) -> None:
        selected_batch, selected_actions = batch_tree.selection(), action_tree.selection()
        if not selected_batch or not selected_actions:
            messagebox.showinfo(
                "Selection required", "Select a batch and at least one file to restore."
            )
            return
        groups = {action_groups[row] for row in selected_actions if row in action_groups}
        if groups and self._confirm_restore(len(groups), "selected file(s)"):
            self._finish_restore(window, undo_batch(selected_batch[0], decision_group_ids=groups))

    def _restore_entire_history(self, window, batch_tree) -> None:
        selected = batch_tree.selection()
        if not selected:
            messagebox.showinfo("Selection required", "Select a batch to restore.")
            return
        if self._confirm_restore(None, "entire batch"):
            self._finish_restore(window, undo_batch(selected[0]))

    def _confirm_restore(self, count: int | None, scope: str) -> bool:
        label = f"{count} {scope}" if count is not None else scope
        return messagebox.askyesno(f"Restore {scope}", f"Restore {label} from batch history?")

    def _finish_restore(self, window, results) -> None:
        succeeded = sum(result.status == "succeeded" for result in results)
        messagebox.showinfo("Restoration complete", f"Successfully restored {succeeded} file(s).")
        window.destroy()
        self.status_var.set(f"Restored {succeeded} file(s) from history.")


__all__ = ["HistoryDialogMixin"]
