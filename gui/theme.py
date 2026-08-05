"""Visual constants and theme application for the desktop interface."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

_FIXED_TREE_COLUMNS = {"selected", "action", "confidence", "status"}
_TREE_STYLE = "Ballad.Treeview"
_ACTIVITY_SIDEBAR_WIDTH = 360
_ACTIVITY_COLLAPSED_WIDTH = 82
_SHIFT_MASK = 0x0001
_SHARED_ARTWORK_PREVIEW_LIMIT = 8
_SHARED_ARTWORK_NAMES = {
    "albumart.jpg",
    "albumartsmall.jpg",
    "cover.jpg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "front.jpg",
    "front.png",
}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class ThemePalette:
    """Colors used by native Tk widgets and ttk styles."""

    root_background: str
    surface_background: str
    alternate_surface: str
    field_background: str
    heading_background: str
    text: str
    muted_text: str
    disabled_text: str
    border: str
    selected_background: str
    selected_text: str
    primary_button: str
    primary_button_active: str
    primary_button_disabled: str
    error_text: str
    warning_text: str
    success_text: str
    warning_row_background: str
    warning_row_text: str
    error_row_background: str
    error_row_text: str
    tooltip_background: str
    tooltip_text: str


_DARK_PALETTE = ThemePalette(
    root_background="#1e1e1e",
    surface_background="#252526",
    alternate_surface="#2d2d30",
    field_background="#1e1e1e",
    heading_background="#333337",
    text="#f1f1f1",
    muted_text="#b8b8b8",
    disabled_text="#777777",
    border="#4b4b4b",
    selected_background="#264f78",
    selected_text="#ffffff",
    primary_button="#238636",
    primary_button_active="#2ea043",
    primary_button_disabled="#5d8067",
    error_text="#ff8a80",
    warning_text="#f3d68a",
    success_text="#73c991",
    warning_row_background="#4f4523",
    warning_row_text="#f3d68a",
    error_row_background="#512b2b",
    error_row_text="#ffb4ab",
    tooltip_background="#333333",
    tooltip_text="#ffffff",
)

_LIGHT_PALETTE = ThemePalette(
    root_background="#f0f0f0",
    surface_background="#f0f0f0",
    alternate_surface="#e8e8e8",
    field_background="#ffffff",
    heading_background="#d9d9d9",
    text="#202020",
    muted_text="#666666",
    disabled_text="#888888",
    border="#b8b8b8",
    selected_background="#4a90e2",
    selected_text="#ffffff",
    primary_button="#238636",
    primary_button_active="#2ea043",
    primary_button_disabled="#d3f4dc",
    error_text="#d9534f",
    warning_text="#664d03",
    success_text="#198754",
    warning_row_background="#fff3cd",
    warning_row_text="#664d03",
    error_row_background="#f8d7da",
    error_row_text="#842029",
    tooltip_background="#ffffe0",
    tooltip_text="#202020",
)

_CONFIDENCE_ROW_STYLES = {
    "low": (_DARK_PALETTE.error_row_background, _DARK_PALETTE.error_row_text),
    "review": (_DARK_PALETTE.error_row_background, _DARK_PALETTE.error_row_text),
    "error": (_DARK_PALETTE.error_row_background, _DARK_PALETTE.error_row_text),
    "blocked": (_DARK_PALETTE.error_row_background, _DARK_PALETTE.error_row_text),
    "mixed": (_DARK_PALETTE.warning_row_background, _DARK_PALETTE.warning_row_text),
    "medium": (_DARK_PALETTE.warning_row_background, _DARK_PALETTE.warning_row_text),
    "warning": (_DARK_PALETTE.warning_row_background, _DARK_PALETTE.warning_row_text),
}


def get_theme_palette(mode: str) -> ThemePalette:
    """Return the selected palette, defaulting to dark mode."""
    return _LIGHT_PALETTE if mode == "light" else _DARK_PALETTE


def confidence_row_styles(mode: str) -> dict[str, tuple[str, str]]:
    """Return row colors for the selected theme."""
    palette = get_theme_palette(mode)
    return {
        "low": (palette.error_row_background, palette.error_row_text),
        "review": (palette.error_row_background, palette.error_row_text),
        "error": (palette.error_row_background, palette.error_row_text),
        "blocked": (palette.error_row_background, palette.error_row_text),
        "mixed": (palette.warning_row_background, palette.warning_row_text),
        "medium": (palette.warning_row_background, palette.warning_row_text),
        "warning": (palette.warning_row_background, palette.warning_row_text),
    }


def confidence_style(confidence: object) -> str:
    """Return the semantic label style for a proposal confidence."""
    normalized = str(confidence).casefold()
    if normalized == "high":
        return "Ballad.Success.TLabel"
    if normalized in {"medium", "review", "mixed", "warning"}:
        return "Ballad.Warning.TLabel"
    return "Ballad.Error.TLabel"


def get_widget_theme_mode(widget: tk.Misc) -> str:
    """Return the current mode for a widget's top-level window."""
    root = widget.winfo_toplevel()
    return getattr(root, "_ballad_theme_mode", "dark")


def apply_theme(root: tk.Misc, mode: str) -> ThemePalette:
    """Apply a palette to existing widgets and shared ttk styles."""
    palette = get_theme_palette(mode)
    root._ballad_theme_mode = "dark" if mode == "dark" else "light"
    style = ttk.Style(root)
    _use_controllable_theme(style)
    _configure_styles(style, palette)
    _refresh_widgets(root, palette, root._ballad_theme_mode)
    return palette


def _use_controllable_theme(style: ttk.Style) -> None:
    try:
        style.theme_use("clam")
    except tk.TclError:
        return


def _configure_styles(style: ttk.Style, palette: ThemePalette) -> None:
    style.configure("TFrame", background=palette.surface_background)
    style.configure("TLabel", background=palette.surface_background, foreground=palette.text)
    style.configure(
        "TLabelframe",
        background=palette.surface_background,
        bordercolor=palette.border,
    )
    style.configure(
        "TLabelframe.Label",
        background=palette.surface_background,
        foreground=palette.text,
    )
    style.configure("TCheckbutton", background=palette.surface_background, foreground=palette.text)
    style.configure(
        "TButton",
        background=palette.alternate_surface,
        foreground=palette.text,
        bordercolor=palette.border,
    )
    style.configure(
        "TEntry",
        fieldbackground=palette.field_background,
        foreground=palette.text,
        bordercolor=palette.border,
    )
    style.configure("TNotebook", background=palette.root_background, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=palette.alternate_surface,
        foreground=palette.text,
        padding=(10, 4),
    )
    style.configure(
        "TScrollbar", background=palette.alternate_surface, troughcolor=palette.root_background
    )
    style.configure("TPanedwindow", background=palette.root_background)
    _configure_tree_styles(style, palette)
    _configure_semantic_label_styles(style, palette)
    _configure_style_maps(style, palette)


def _configure_tree_styles(style: ttk.Style, palette: ThemePalette) -> None:
    tree_options = {
        "background": palette.field_background,
        "fieldbackground": palette.field_background,
        "foreground": palette.text,
        "bordercolor": palette.border,
        "rowheight": 24,
    }
    style.configure("Treeview", **tree_options)
    style.configure(_TREE_STYLE, **tree_options)
    style.configure(
        "Treeview.Heading",
        background=palette.heading_background,
        foreground=palette.text,
        bordercolor=palette.border,
    )


def _configure_semantic_label_styles(style: ttk.Style, palette: ThemePalette) -> None:
    style.configure("Ballad.Muted.TLabel", foreground=palette.muted_text)
    style.configure("Ballad.Error.TLabel", foreground=palette.error_text)
    style.configure("Ballad.Warning.TLabel", foreground=palette.warning_text)
    style.configure("Ballad.Success.TLabel", foreground=palette.success_text)
    style.configure(
        "Ballad.Tooltip.TLabel",
        background=palette.tooltip_background,
        foreground=palette.tooltip_text,
        relief=tk.SOLID,
        borderwidth=1,
        padding=(6, 3),
    )


def _configure_style_maps(style: ttk.Style, palette: ThemePalette) -> None:
    style.map(
        "TButton",
        background=[
            ("disabled", palette.alternate_surface),
            ("pressed", palette.primary_button_active),
            ("active", palette.alternate_surface),
        ],
        foreground=[("disabled", palette.disabled_text)],
    )
    style.map(
        "TCheckbutton",
        background=[("active", palette.alternate_surface)],
        foreground=[("disabled", palette.disabled_text)],
    )
    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", palette.selected_background),
            ("active", palette.alternate_surface),
        ],
        foreground=[("selected", palette.selected_text)],
    )
    style.map(
        "Treeview",
        background=[("selected", palette.selected_background)],
        foreground=[("selected", palette.selected_text)],
    )


def _refresh_widgets(root: tk.Misc, palette: ThemePalette, mode: str) -> None:
    _configure_native_widget(root, palette)
    if isinstance(root, ttk.Treeview):
        _configure_tree_tags(root, mode)
    for child in root.winfo_children():
        _refresh_widgets(child, palette, mode)


def _configure_native_widget(widget: tk.Misc, palette: ThemePalette) -> None:
    if isinstance(widget, (tk.Tk, tk.Toplevel)):
        widget.configure(background=palette.root_background)
    elif isinstance(widget, tk.Frame):
        widget.configure(background=palette.surface_background)
    elif isinstance(widget, tk.Text):
        widget.configure(
            background=palette.field_background,
            foreground=palette.text,
            insertbackground=palette.text,
            selectbackground=palette.selected_background,
            selectforeground=palette.selected_text,
        )
    elif isinstance(widget, tk.Canvas):
        widget.configure(
            background=palette.surface_background,
            highlightbackground=palette.border,
            highlightcolor=palette.border,
        )
    elif isinstance(widget, tk.Button):
        widget.configure(
            background=palette.primary_button,
            activebackground=palette.primary_button_active,
            foreground=palette.selected_text,
            activeforeground=palette.selected_text,
            disabledforeground=palette.primary_button_disabled,
        )


def _configure_tree_tags(tree: ttk.Treeview, mode: str) -> None:
    for level, (background, foreground) in confidence_row_styles(mode).items():
        tree.tag_configure(f"conf-{level}", background=background, foreground=foreground)


def _confidence_row_tags(confidence: str) -> tuple[str, ...]:
    """Map a confidence level to its optional Treeview tag."""
    if confidence in _CONFIDENCE_ROW_STYLES:
        return (f"conf-{confidence}",)
    return ()


__all__ = [
    "_ACTIVITY_COLLAPSED_WIDTH",
    "_ACTIVITY_SIDEBAR_WIDTH",
    "_CONFIDENCE_ROW_STYLES",
    "_FIXED_TREE_COLUMNS",
    "_IMAGE_EXTENSIONS",
    "_SHARED_ARTWORK_NAMES",
    "_SHARED_ARTWORK_PREVIEW_LIMIT",
    "_SHIFT_MASK",
    "_TREE_STYLE",
    "_confidence_row_tags",
    "ThemePalette",
    "apply_theme",
    "confidence_style",
    "confidence_row_styles",
    "get_theme_palette",
    "get_widget_theme_mode",
]
