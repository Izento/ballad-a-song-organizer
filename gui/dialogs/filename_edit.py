"""Filename correction dialog for one reviewed rename."""

from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, ttk


class _FilenameDialog(simpledialog.Dialog):
    """Ask for a corrected filename without changing its extension."""

    def __init__(self, parent, initialvalue: str):
        self.initialvalue = initialvalue
        self.result: str | None = None
        super().__init__(parent, title="Correct proposed filename")

    def body(self, master):
        self.minsize(680, 120)
        self.resizable(True, False)
        ttk.Label(master, text="Filename to use:").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 8)
        )
        self.entry = ttk.Entry(master, width=80)
        self.entry.insert(0, self.initialvalue)
        self.entry.grid(row=1, column=0, sticky=tk.EW)
        master.columnconfigure(0, weight=1)
        return self.entry

    def apply(self):
        self.result = self.entry.get()


def _ask_filename(parent, initialvalue: str) -> str | None:
    """Open the filename editor and return the accepted value."""
    return _FilenameDialog(parent, initialvalue).result


__all__ = ["_FilenameDialog", "_ask_filename"]
