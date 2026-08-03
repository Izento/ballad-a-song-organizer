"""Activity log and review-inspector sidebar widgets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from gui.theme import _ACTIVITY_COLLAPSED_WIDTH, _ACTIVITY_SIDEBAR_WIDTH
from gui.widgets.tooltip import _add_tooltip


class ActivitySidebarMixin:
    """Build and control the collapsible review sidebar."""

    def _build_activity_sidebar(self, parent: ttk.Frame) -> None:
        self._preview_images: list[Any] = []
        self.activity_container = ttk.Frame(parent, width=_ACTIVITY_SIDEBAR_WIDTH)
        self.activity_container.grid(row=0, column=1, sticky=tk.NSEW, padx=(8, 0))
        self.activity_container.grid_propagate(False)
        self.activity_container.columnconfigure(0, weight=1)
        self.activity_container.rowconfigure(0, weight=1)
        self._build_sidebar_panel()
        self._build_sidebar_show_button()
        self._update_review_details(None)

    def _build_sidebar_panel(self) -> None:
        self.sidebar_panel = ttk.Frame(self.activity_container)
        self.sidebar_panel.grid(row=0, column=0, sticky=tk.NSEW)
        self.sidebar_panel.columnconfigure(0, weight=1)
        self.sidebar_panel.rowconfigure(0, weight=1)
        self.sidebar_notebook = ttk.Notebook(self.sidebar_panel)
        self.sidebar_notebook.grid(row=0, column=0, sticky=tk.NSEW)
        self._build_activity_tab()
        self._build_review_details_tab()

    def _build_activity_tab(self) -> None:
        self.activity_tab = ttk.Frame(self.sidebar_notebook, padding=6)
        self.sidebar_notebook.add(self.activity_tab, text="Activity")
        header = ttk.Frame(self.activity_tab)
        header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header, text="Live progress", font=("TkDefaultFont", 9, "bold")).pack(
            side=tk.LEFT
        )
        self._add_collapse_button(header)
        self._build_activity_log(self.activity_tab)

    def _add_collapse_button(self, parent: ttk.Frame) -> None:
        button = ttk.Button(
            parent, text="Collapse", command=self._toggle_activity_sidebar
        )
        button.pack(side=tk.RIGHT)
        _add_tooltip(button, "Collapse the activity sidebar.")

    def _build_activity_log(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.activity_log = tk.Text(
            frame, wrap=tk.WORD, state=tk.DISABLED, font="TkFixedFont", padx=6, pady=6
        )
        self.activity_log.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.activity_log.yview)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.activity_log.configure(yscrollcommand=scrollbar.set)

    def _build_review_details_tab(self) -> None:
        self.details_tab = ttk.Frame(self.sidebar_notebook, padding=6)
        self.sidebar_notebook.add(self.details_tab, text="Review details")
        header = ttk.Frame(self.details_tab)
        header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(header, text="Proposal Inspector", font=("TkDefaultFont", 9, "bold")).pack(
            side=tk.LEFT
        )
        self._add_collapse_button(header)
        self._build_details_scroll_area()

    def _build_details_scroll_area(self) -> None:
        frame = ttk.Frame(self.details_tab)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.details_canvas = tk.Canvas(frame, highlightthickness=0)
        self.details_scrollbar = ttk.Scrollbar(
            frame, orient=tk.VERTICAL, command=self.details_canvas.yview
        )
        self.details_content = ttk.Frame(self.details_canvas)
        self.details_content.bind("<Configure>", self._resize_details_scroll_region)
        self.details_canvas_window = self.details_canvas.create_window(
            (0, 0), window=self.details_content, anchor="nw"
        )
        self.details_canvas.configure(yscrollcommand=self.details_scrollbar.set)
        self.details_canvas.bind("<Configure>", self._resize_details_width)
        self.details_canvas.grid(row=0, column=0, sticky=tk.NSEW)
        self.details_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.details_canvas.bind_all("<MouseWheel>", self._scroll_review_details)

    def _resize_details_scroll_region(self, _event) -> None:
        self.details_canvas.configure(scrollregion=self.details_canvas.bbox("all"))

    def _resize_details_width(self, event) -> None:
        self.details_canvas.itemconfig(self.details_canvas_window, width=event.width)

    def _scroll_review_details(self, event) -> None:
        self.details_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _build_sidebar_show_button(self) -> None:
        self.activity_show_button = ttk.Button(
            self.activity_container,
            text="Show sidebar",
            command=self._toggle_activity_sidebar,
            padding=(2, 2),
        )
        _add_tooltip(
            self.activity_show_button, "Show the live activity & review sidebar."
        )

    def _toggle_activity_sidebar(self) -> None:
        if self._activity_sidebar_open:
            self.sidebar_panel.grid_remove()
            self.activity_show_button.grid(row=0, column=0, sticky=tk.N, pady=(8, 0))
            self.activity_container.configure(width=_ACTIVITY_COLLAPSED_WIDTH)
        else:
            self.activity_show_button.grid_remove()
            self.sidebar_panel.grid(row=0, column=0, sticky=tk.NSEW)
            self.activity_container.configure(width=_ACTIVITY_SIDEBAR_WIDTH)
        self._activity_sidebar_open = not self._activity_sidebar_open

    def _clear_activity_log(self) -> None:
        self.activity_log.configure(state=tk.NORMAL)
        self.activity_log.delete("1.0", tk.END)
        self.activity_log.configure(state=tk.DISABLED)

    def _append_activity_log(self, message: str) -> None:
        follow_tail = self.activity_log.yview()[1] >= 0.999
        self.activity_log.configure(state=tk.NORMAL)
        self.activity_log.insert(tk.END, f"{message}\n")
        if follow_tail:
            self.activity_log.see(tk.END)
        self.activity_log.configure(state=tk.DISABLED)


__all__ = ["ActivitySidebarMixin"]
