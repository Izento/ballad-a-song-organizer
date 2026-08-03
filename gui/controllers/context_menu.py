"""Review-row context menu and operating-system launch commands."""

from __future__ import annotations

import json
import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


class ContextMenuMixin:
    """Expose playback, file location, evidence, and quarantine actions."""

    def _handle_tree_context_menu(self, tree_name: str, event):
        tree = self.trees[tree_name]
        row = tree.identify_row(event.y)
        if not row:
            return "break"
        if row not in tree.selection():
            tree.selection_set(row)
        path = self.session.row_paths.get((tree_name, row))
        if not path:
            return "break"
        menu = self._file_context_menu(path)
        proposals = self._selected_context_proposals(tree_name, tree, row)
        self._add_proposal_context_commands(menu, tree_name, proposals)
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _file_context_menu(self, path: str):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Play", command=lambda: self._open_with_default_app(path))
        menu.add_command(
            label="Open in File Explorer", command=lambda: self._open_in_file_explorer(path)
        )
        return menu

    def _selected_context_proposals(self, tree_name: str, tree, row: str) -> list:
        rows = list(tree.selection()) if row in tree.selection() else [row]
        proposals, seen = [], set()
        for selected_row in rows:
            item_id = self.session.row_ids.get((tree_name, selected_row), "")
            proposal = self.session.proposal_for_id(item_id) if item_id not in seen else None
            if proposal is not None:
                seen.add(item_id)
                proposals.append(proposal)
        return proposals

    def _add_proposal_context_commands(self, menu, tree_name: str, proposals: list) -> None:
        if not proposals:
            return
        menu.add_separator()
        label = (
            "Ignore this match in future" if len(proposals) == 1
            else f"Ignore {len(proposals)} selected matches in future"
        )
        menu.add_command(label=label, command=lambda: self._quarantine_proposals(proposals))
        menu.add_command(label="Quarantine manager…", command=self._show_quarantine_manager)
        proposal = proposals[0]
        if tree_name == "tags" and proposal.evidence:
            menu.add_separator()
            menu.add_command(
                label="Show metadata evidence",
                command=lambda: self._show_metadata_evidence(proposal),
            )

    def _show_metadata_evidence(self, proposal) -> None:
        messagebox.showinfo(
            "Metadata evidence",
            json.dumps(proposal.evidence.to_dict(), indent=2, ensure_ascii=False),
        )

    def _open_in_file_explorer(self, path: str) -> None:
        target = Path(path)
        if not target.is_file():
            messagebox.showwarning("File unavailable", f"This file is no longer available:\n{target}")
            return
        try:
            self._launch_file_explorer(target)
        except OSError as exc:
            messagebox.showerror("Could not open File Explorer", str(exc))

    def _launch_file_explorer(self, target: Path) -> None:
        if os.name == "nt":
            subprocess.Popen(
                ["explorer.exe", "/select,", str(target)],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])

    def _open_with_default_app(self, path: str) -> None:
        target = Path(path)
        if not target.is_file():
            messagebox.showwarning("File unavailable", f"This file is no longer available:\n{target}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(target))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            messagebox.showerror("Could not open file", str(exc))


__all__ = ["ContextMenuMixin"]
