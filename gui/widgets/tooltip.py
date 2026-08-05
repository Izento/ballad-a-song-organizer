"""Minimal delayed tooltips for dense Tk controls."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class _Tooltip:
    """Show a short description when one widget is hovered."""

    _DELAY_MS = 450

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel_pending()
        self._after_id = self.widget.after(self._DELAY_MS, self._show)

    def _cancel_pending(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self.widget.winfo_viewable():
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._make_label().pack()

    def _make_label(self) -> ttk.Label:
        return ttk.Label(
            self._tip,
            text=self.text,
            style="Ballad.Tooltip.TLabel",
            wraplength=320,
            justify=tk.LEFT,
        )

    def _hide(self, _event=None) -> None:
        self._cancel_pending()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def _add_tooltip(widget: tk.Widget, text: str) -> None:
    """Attach an unmanaged tooltip to a widget."""
    _Tooltip(widget, text)


__all__ = ["_Tooltip", "_add_tooltip"]
