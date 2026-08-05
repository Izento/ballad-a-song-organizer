"""Quarantine manager dialog."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from gui.presentation import format_local_timestamp
from gui.theme import apply_theme, get_widget_theme_mode
from renamer.quarantine import load_quarantine, unquarantine_files
from renamer.review_models import path_key


class QuarantineDialogMixin:
    """Show persisted ignored matches and allow a user to restore them."""

    def _show_quarantine_manager(self) -> None:
        window, tree = self._build_quarantine_window()
        self._populate_quarantine_tree(tree)
        self._add_quarantine_buttons(window, tree)

    def _build_quarantine_window(self):
        window = tk.Toplevel(self.root)
        apply_theme(window, get_widget_theme_mode(self.root))
        window.title("Manage quarantine")
        window.geometry("780x380")
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            frame,
            columns=("file", "artist_title", "date"),
            show="headings",
            selectmode="extended",
        )
        self._configure_quarantine_tree(tree)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        return window, tree

    def _configure_quarantine_tree(self, tree) -> None:
        for column, title, width in (
            ("file", "File", 320),
            ("artist_title", "Ignored Match", 280),
            ("date", "Date", 140),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width)

    def _populate_quarantine_tree(self, tree) -> None:
        tree.delete(*tree.get_children())
        for item in load_quarantine():
            path = item.get("path", "")
            identity = " / ".join(filter(None, (item.get("artist"), item.get("title"))))
            tree.insert(
                "",
                tk.END,
                iid=path_key(path),
                values=(
                    Path(path).name,
                    identity or "Ignored",
                    format_local_timestamp(item.get("created_at", "")),
                ),
            )

    def _add_quarantine_buttons(self, window, tree) -> None:
        bottom = ttk.Frame(window, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        ttk.Button(
            bottom,
            text="Restore selected",
            command=lambda: self._restore_selected_quarantine(tree),
        ).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Close", command=window.destroy).pack(side=tk.RIGHT)

    def _restore_selected_quarantine(self, tree) -> None:
        selected = tree.selection()
        if not selected:
            return
        removed = unquarantine_files(list(selected))
        if removed:
            self._populate_quarantine_tree(tree)
            self.status_var.set(f"Restored {removed} file(s) from quarantine.")


__all__ = ["QuarantineDialogMixin"]
