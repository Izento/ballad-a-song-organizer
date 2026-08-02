"""Review-first tkinter application."""

from __future__ import annotations

import json
import os
import queue
import subprocess
from dataclasses import replace
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from renamer.apply import (
    batch_history,
    batches_requiring_recovery,
    latest_undoable_batch,
)
from renamer.review_api import (
    coordinate_tag_proposals,
    refresh_rename_readiness,
)
from renamer.review_models import (
    ReviewPlan,
    canonical_path,
    path_key,
    proposal_id,
)
from renamer.musicbrainz import is_available as musicbrainz_available
from renamer.runtime import (
    ensure_app_dirs,
    resolve_acoustid_key,
    resolve_fpcalc,
    resource_path,
)
from renamer.selection import (
    action_items as _action_items,
    expand_group_selection as _expand_group_selection,
    grouped_action_ids as _grouped_action_ids,
    ready_ids as _ready_ids,
    recommended_ids as _recommended_ids,
    requires_review as _requires_review,
)
from gui.workers import BackgroundJobs
from gui.presentation import (
    filename_validation_error as _filename_validation_error,
    format_local_timestamp as _format_local_timestamp,
    plan_rows,
    tag_display as _tag_display,
)


GUI_TITLE = "Ballad"
_WINDOWS_APP_ID = "Ballad.SongOrganizer"
_FIXED_TREE_COLUMNS = {"selected", "action", "confidence"}
_TREE_STYLE = "Ballad.Treeview"
# Green reads as "go / confirm" rather than a neutral link color, and this
# button is the single call-to-action driving the whole review workflow.
_PRIMARY_BUTTON_BG = "#238636"
_PRIMARY_BUTTON_ACTIVE_BG = "#2ea043"
_PRIMARY_BUTTON_DISABLED_FG = "#d3f4dc"
_ACTIVITY_SIDEBAR_WIDTH = 320
_ACTIVITY_COLLAPSED_WIDTH = 82
_SHIFT_MASK = 0x0001
# Rows worth a second look shouldn't blend in with everything that's safe to
# accept at a glance. "review" and "error" share the "low" styling because
# they represent the same thing to a user scanning the grid: don't trust
# this one without looking closer.
_CONFIDENCE_ROW_STYLES = {
    "low": ("#f8d7da", "#842029"),
    "review": ("#f8d7da", "#842029"),
    "error": ("#f8d7da", "#842029"),
    "medium": ("#fff3cd", "#664d03"),
    "warning": ("#fff3cd", "#664d03"),
}


def _confidence_row_tags(confidence: str) -> tuple[str, ...]:
    if confidence in _CONFIDENCE_ROW_STYLES:
        return (f"conf-{confidence}",)
    return ()


def _format_progress_log(
    stage: str,
    current: int,
    total: int,
    path: str,
) -> str:
    location = path or "working"
    return f"{stage}: {current}/{total}  {location}"


class _Tooltip:
    """Minimal hover tooltip for a single widget.

    Several controls here (e.g. the duplicate-fingerprinting checkbox) have
    a name that's easy to misread as controlling more than it does. A short
    delayed tooltip clarifies scope without permanently cluttering the
    layout with explanatory labels.
    """

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
        ttk.Label(
            self._tip,
            text=self.text,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            padding=(6, 3),
            wraplength=320,
            justify=tk.LEFT,
        ).pack()

    def _hide(self, _event=None) -> None:
        self._cancel_pending()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def _add_tooltip(widget: tk.Widget, text: str) -> None:
    _Tooltip(widget, text)


class _FilenameDialog(simpledialog.Dialog):
    def __init__(self, parent, initialvalue: str):
        self.initialvalue = initialvalue
        self.result: str | None = None
        super().__init__(parent, title="Correct proposed filename")

    def body(self, master):
        self.minsize(680, 120)
        self.resizable(True, False)
        ttk.Label(master, text="Filename to use:").grid(
            row=0,
            column=0,
            sticky=tk.W,
            padx=(0, 8),
            pady=(0, 8),
        )
        self.entry = ttk.Entry(master, width=80)
        self.entry.insert(0, self.initialvalue)
        self.entry.grid(row=1, column=0, sticky=tk.EW)
        master.columnconfigure(0, weight=1)
        return self.entry

    def apply(self):
        self.result = self.entry.get()


def _ask_filename(parent, initialvalue: str) -> str | None:
    return _FilenameDialog(parent, initialvalue).result


def _set_windows_app_identity() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id(ctypes.c_wchar_p(_WINDOWS_APP_ID))
    except (AttributeError, OSError, TypeError):
        return


class SongOrganizerApp:
    def __init__(self, root: tk.Tk | None = None):
        _set_windows_app_identity()
        self.root = root or tk.Tk()
        self._icon_handles: tuple[object, ...] = ()
        self.root.title(GUI_TITLE)
        self._set_window_icon()
        self.root.geometry("1180x720")
        self.root.minsize(900, 560)
        self.jobs = BackgroundJobs()
        self.events = self.jobs.events
        self.plan: ReviewPlan | None = None
        self.selected_ids: set[str] = set()
        self._applied_group_ids: set[str] = set()
        self._row_ids: dict[tuple[str, str], str] = {}
        self._row_paths: dict[tuple[str, str], str] = {}
        self._tree_headings: dict[str, dict[str, str]] = {}
        self._sort_state: dict[tuple[str, str], bool] = {}
        self._selection_anchors: dict[str, str] = {}
        self.activity_container: ttk.Frame
        self.activity_panel: ttk.Labelframe
        self.activity_log: tk.Text
        self.activity_show_button: ttk.Button
        self._recovery_overrides: set[str] = set()

        self.folder_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        fpcalc_path = resolve_fpcalc()
        self.fpcalc_available = fpcalc_path is not None
        self.duplicate_check_var = tk.BooleanVar(value=True)
        self.duplicate_check_var.trace_add(
            "write", self._sync_fingerprint_availability
        )
        self.fingerprint_var = tk.BooleanVar(value=fpcalc_path is not None)
        self._last_run_checked_duplicates = True
        self.status_var = tk.StringVar(value="Choose a folder to begin.")
        self._activity_sidebar_open = True
        self.acoustid_key = resolve_acoustid_key()
        self.musicbrainz_available = musicbrainz_available()
        self.online_identification_var = tk.BooleanVar(
            value=bool(self.acoustid_key and fpcalc_path)
        )
        self.cover_art_var = tk.BooleanVar(value=True)
        fpcalc_state = (
            "available" if fpcalc_path else "not installed (optional)"
        )
        online_state = (
            "ready"
            if self.acoustid_key and fpcalc_path and self.musicbrainz_available
            else "MusicBrainz client missing"
            if not self.musicbrainz_available
            else "embedded IDs only"
        )
        self.capability_var = tk.StringVar(
            value=(
                f"Fingerprint helper: {fpcalc_state} | "
                f"Online identification: {online_state}"
            )
        )
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_events)

    def _set_window_icon(self) -> None:
        icon_path = resource_path("ballad.ico")
        if not icon_path.is_file():
            return
        try:
            self.root.iconbitmap(str(icon_path))
        except tk.TclError:
            pass
        try:
            self.root.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass
        if os.name == "nt":
            self._set_windows_icon_handles(icon_path)

    def _set_windows_icon_handles(self, icon_path: Path) -> None:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            load_image = user32.LoadImageW
            load_image.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            load_image.restype = ctypes.c_void_p
            send_message = user32.SendMessageW
            send_message.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_size_t,
                ctypes.c_void_p,
            ]
            send_message.restype = ctypes.c_void_p
            hwnd = ctypes.c_void_p(self.root.winfo_id())
            handles = []
            for icon_size, icon_kind in ((32, 1), (16, 0)):
                handle = load_image(
                    None,
                    str(icon_path),
                    1,
                    icon_size,
                    icon_size,
                    0x10,
                )
                if handle:
                    send_message(hwnd, 0x0080, icon_kind, handle)
                    handles.append(handle)
            self._icon_handles = tuple(handles)
        except (AttributeError, OSError, TypeError):
            self._icon_handles = ()

    def _build_ui(self) -> None:
        library = ttk.Labelframe(self.root, text="Library", padding=10)
        library.pack(fill=tk.X, padx=10, pady=(10, 6))
        folder_row = ttk.Frame(library)
        folder_row.pack(fill=tk.X)
        ttk.Label(folder_row, text="Music folder:").pack(side=tk.LEFT)
        ttk.Entry(folder_row, textvariable=self.folder_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 5)
        )
        ttk.Button(folder_row, text="Browse…", command=self._browse).pack(
            side=tk.LEFT
        )
        ttk.Checkbutton(
            folder_row,
            text="Include subfolders",
            variable=self.recursive_var,
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(library, textvariable=self.capability_var).pack(
            anchor=tk.W, pady=(6, 0)
        )

        options = ttk.Labelframe(
            self.root, text="Identification & duplicate detection", padding=10
        )
        options.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.online_identification_check = ttk.Checkbutton(
            options,
            text="Use AcoustID identification",
            variable=self.online_identification_var,
            state=(
                tk.NORMAL
                if self.acoustid_key and self.fpcalc_available
                else tk.DISABLED
            ),
        )
        self.online_identification_check.pack(side=tk.LEFT)
        _add_tooltip(
            self.online_identification_check,
            "Identify songs with missing or unreliable tags by audio "
            "fingerprint via AcoustID, then look up the match in "
            "MusicBrainz. Requires an AcoustID API key and the fpcalc "
            "helper tool.",
        )
        self.cover_art_check = ttk.Checkbutton(
            options,
            text="Embed missing front cover art",
            variable=self.cover_art_var,
        )
        self.cover_art_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.cover_art_check,
            "Download and embed front cover art from the Cover Art Archive "
            "for files that don't already have any.",
        )
        self.duplicate_check_check = ttk.Checkbutton(
            options,
            text="Check for duplicate files",
            variable=self.duplicate_check_var,
        )
        self.duplicate_check_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.duplicate_check_check,
            "Hashes every file to find exact and same-song duplicates. If "
            "you already know this folder has no duplicates (just messy "
            "filenames), turning this off skips hashing every file and can "
            "noticeably speed up large libraries.",
        )
        self.fingerprint_check = ttk.Checkbutton(
            options,
            text="Fingerprint audio for stronger duplicate matches",
            variable=self.fingerprint_var,
        )
        self.fingerprint_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.fingerprint_check,
            "Duplicate detection always runs (exact file and title/artist "
            "matches). Enabling this additionally computes an acoustic "
            "fingerprint per file so re-encoded or transcoded copies are "
            "still caught, not just identical files. Slower on large "
            "libraries. Has no effect while duplicate checking is off.",
        )
        self._sync_fingerprint_availability()

        content = ttk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(content)
        self.notebook.grid(row=0, column=0, sticky=tk.NSEW)
        self.trees: dict[str, ttk.Treeview] = {}
        for key, title in (
            ("renames", "Filename changes"),
            ("tags", "Metadata changes"),
            ("duplicates", "Duplicate findings (read-only)"),
            ("errors", "Skipped / errors"),
        ):
            frame = ttk.Frame(self.notebook, padding=6)
            self.notebook.add(frame, text=title)
            tree = self._make_tree(frame, key)
            self.trees[key] = tree

        self._build_activity_sidebar(content)

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        bottom.columnconfigure(1, weight=1)
        selection_controls = ttk.Frame(bottom)
        selection_controls.grid(row=0, column=0, sticky=tk.W)
        ttk.Button(
            selection_controls,
            text="Select recommended",
            command=self._select_recommended,
        ).pack(side=tk.LEFT)
        ttk.Button(
            selection_controls,
            text="Select all ready",
            command=self._select_all,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.edit_button = ttk.Button(
            selection_controls,
            text="Edit filename",
            command=self._edit_selected_filename,
        )
        self.edit_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(bottom, textvariable=self.status_var).grid(
            row=0,
            column=1,
            sticky=tk.EW,
            padx=12,
        )
        secondary_actions = ttk.Frame(bottom)
        secondary_actions.grid(row=0, column=2, sticky=tk.E)
        self.cancel_button = ttk.Button(
            secondary_actions, text="Cancel", command=self._cancel
        )
        self.cancel_button.pack(side=tk.LEFT)
        self.history_button = ttk.Button(
            secondary_actions, text="History", command=self._show_history
        )
        self.history_button.pack(side=tk.LEFT)
        self.undo_button = ttk.Button(
            secondary_actions, text="Undo latest", command=self._undo_latest
        )
        self.undo_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Separator(secondary_actions, orient=tk.VERTICAL).pack(
            side=tk.LEFT,
            fill=tk.Y,
            padx=10,
        )
        self.primary_button = tk.Button(
            secondary_actions,
            text="Organize library",
            command=self._organize_library,
            background=_PRIMARY_BUTTON_BG,
            activebackground=_PRIMARY_BUTTON_ACTIVE_BG,
            foreground="white",
            activeforeground="white",
            disabledforeground=_PRIMARY_BUTTON_DISABLED_FG,
            font=("TkDefaultFont", 10, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
            padx=14,
            pady=3,
        )
        self.primary_button.pack(side=tk.LEFT)
        self._update_primary_button()

    def _build_activity_sidebar(self, parent: ttk.Frame) -> None:
        self.activity_container = ttk.Frame(
            parent,
            width=_ACTIVITY_SIDEBAR_WIDTH,
        )
        self.activity_container.grid(
            row=0,
            column=1,
            sticky=tk.NSEW,
            padx=(8, 0),
        )
        self.activity_container.grid_propagate(False)
        self.activity_container.columnconfigure(0, weight=1)
        self.activity_container.rowconfigure(0, weight=1)

        self.activity_panel = ttk.Labelframe(
            self.activity_container,
            text="Activity log",
            padding=6,
        )
        self.activity_panel.grid(row=0, column=0, sticky=tk.NSEW)
        self.activity_panel.columnconfigure(0, weight=1)
        self.activity_panel.rowconfigure(1, weight=1)

        header = ttk.Frame(self.activity_panel)
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Live progress",
        ).grid(row=0, column=0, sticky=tk.W)
        collapse_button = ttk.Button(
            header,
            text="Collapse",
            command=self._toggle_activity_sidebar,
        )
        collapse_button.grid(row=0, column=1, sticky=tk.E)
        _add_tooltip(
            collapse_button,
            "Hide the activity log. The log will remain available until "
            "you show it again.",
        )

        log_frame = ttk.Frame(self.activity_panel)
        log_frame.grid(row=1, column=0, sticky=tk.NSEW)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.activity_log = tk.Text(
            log_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font="TkFixedFont",
            padx=6,
            pady=6,
        )
        self.activity_log.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar = ttk.Scrollbar(
            log_frame,
            orient=tk.VERTICAL,
            command=self.activity_log.yview,
        )
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.activity_log.configure(yscrollcommand=scrollbar.set)

        self.activity_show_button = ttk.Button(
            self.activity_container,
            text="Show log",
            command=self._toggle_activity_sidebar,
            padding=(2, 2),
        )
        _add_tooltip(
            self.activity_show_button,
            "Show the live activity log.",
        )

    def _toggle_activity_sidebar(self) -> None:
        if self._activity_sidebar_open:
            self.activity_panel.grid_remove()
            self.activity_show_button.grid(
                row=0,
                column=0,
                sticky=tk.N,
                pady=(8, 0),
            )
            self.activity_container.configure(width=_ACTIVITY_COLLAPSED_WIDTH)
        else:
            self.activity_show_button.grid_remove()
            self.activity_panel.grid(row=0, column=0, sticky=tk.NSEW)
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

    def _make_tree(self, parent: ttk.Frame, key: str) -> ttk.Treeview:
        if key == "renames":
            columns = ("selected", "action", "current", "proposed", "confidence")
            headings = {
                "selected": "",
                "action": "Action",
                "current": "Current filename",
                "proposed": "Proposed filename",
                "confidence": "Confidence",
            }
            widths = {
                "selected": 26,
                "action": 82,
                "current": 440,
                "proposed": 440,
                "confidence": 72,
            }
        elif key == "tags":
            columns = (
                "selected",
                "action",
                "file",
                "current",
                "proposed",
                "confidence",
            )
            headings = {
                "selected": "",
                "action": "Action",
                "file": "File",
                "current": "Current tags",
                "proposed": "Proposed tags",
                "confidence": "Confidence",
            }
            widths = {
                "selected": 26,
                "action": 82,
                "file": 170,
                "current": 350,
                "proposed": 350,
                "confidence": 72,
            }
        else:
            columns = ("action", "file", "details", "confidence")
            headings = {
                "action": "Action",
                "file": "File",
                "details": "Details",
                "confidence": "Confidence",
            }
            widths = {
                "action": 140,
                "file": 350,
                "details": 420,
                "confidence": 72,
            }
        selectmode = "extended" if key in {"renames", "tags"} else "browse"
        style = ttk.Style(parent)
        style.configure(_TREE_STYLE, rowheight=24)
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode=selectmode,
            style=_TREE_STYLE,
        )
        self._tree_headings[key] = dict(headings)
        for column in columns:
            tree.heading(
                column,
                text=headings[column],
                command=lambda tree_name=key, column=column: self._sort_tree_column(
                    tree_name, column
                ),
            )
            fixed = column in _FIXED_TREE_COLUMNS
            tree.column(
                column,
                width=widths[column],
                minwidth=widths[column] if fixed else 180,
                stretch=not fixed,
                anchor=tk.CENTER if column in {"selected", "confidence"} else tk.W,
            )
        for level, (background, foreground) in _CONFIDENCE_ROW_STYLES.items():
            tree.tag_configure(f"conf-{level}", background=background, foreground=foreground)
        vertical_scrollbar = ttk.Scrollbar(
            tree_frame,
            orient=tk.VERTICAL,
            command=tree.yview,
        )
        horizontal_scrollbar = ttk.Scrollbar(
            tree_frame,
            orient=tk.HORIZONTAL,
            command=tree.xview,
        )
        tree.configure(
            yscrollcommand=vertical_scrollbar.set,
            xscrollcommand=horizontal_scrollbar.set,
        )
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vertical_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        horizontal_scrollbar.grid(row=1, column=0, sticky=tk.EW)
        tree.bind(
            "<Button-1>",
            lambda event, name=key: self._handle_tree_click(name, event),
        )
        tree.bind(
            "<Double-Button-1>",
            lambda event, name=key: self._handle_tree_double_click(name, event),
        )
        tree.bind(
            "<Button-3>",
            lambda event, name=key: self._handle_tree_context_menu(name, event),
        )
        return tree

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="Choose music folder")
        if selected:
            self.folder_var.set(selected)

    def _sync_fingerprint_availability(self, *_args, busy: bool | None = None) -> None:
        """Audio fingerprinting only matters while duplicate checking runs.

        Rather than let it sit checked-but-inert when duplicate checking is
        off (silently doing nothing, which is exactly the kind of ambiguity
        that confused the old "Use fingerprints for duplicate checks"
        label), grey it out so its state visibly reflects whether it can do
        anything. ``busy`` is accepted explicitly because ``_set_busy(True)``
        runs just before the background thread starts, while
        ``self.jobs.active`` would still read False at that instant.
        """
        checking_duplicates = self.duplicate_check_var.get()
        if busy is None:
            busy = self.jobs.active
        self.fingerprint_check.configure(
            state=tk.NORMAL if checking_duplicates and not busy else tk.DISABLED
        )

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.online_identification_check.configure(
            state=(
                tk.DISABLED
                if busy or not (self.acoustid_key and self.fpcalc_available)
                else tk.NORMAL
            )
        )
        self.cover_art_check.configure(state=state)
        self.duplicate_check_check.configure(state=state)
        self._sync_fingerprint_availability(busy=busy)
        self.edit_button.configure(state=state)
        self.cancel_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        self.history_button.configure(state=state)
        self.undo_button.configure(state=state)
        if busy:
            self.primary_button.configure(state=tk.DISABLED)
            self.status_var.set("Working…")
        else:
            self._update_primary_button()

    def _organize_library(self) -> None:
        folder = self.folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Folder required", "Choose an existing music folder.")
            return
        if not self.musicbrainz_available:
            messagebox.showerror(
                "MusicBrainz unavailable",
                "Metadata enrichment cannot run because musicbrainzngs is not "
                "installed in this Python environment. Start Ballad with "
                "'uv run ballad'.",
            )
            return
        if not messagebox.askyesno(
            "Organize library",
            "Ballad will identify songs, enrich metadata, and prepare a "
            "reviewable plan of filename changes, tag changes, and cover "
            "art.\n\nNothing on disk changes until you select proposals "
            "below and click 'Apply selected'. Duplicate findings are "
            "always read-only.\n\nContinue?",
        ):
            return
        self.plan = None
        self.selected_ids.clear()
        self._applied_group_ids = set()
        self._clear_trees()
        self._clear_activity_log()
        self._append_activity_log(f"Starting analysis: {folder}")
        self._set_busy(True)
        self.status_var.set("Organizing library…")
        acoustid_key = (
            self.acoustid_key if self.online_identification_var.get() else None
        )
        self._last_run_checked_duplicates = self.duplicate_check_var.get()
        self.jobs.organize(
            folder,
            recursive=self.recursive_var.get(),
            fingerprint=self.fingerprint_var.get(),
            acoustid_key=acoustid_key,
            include_artwork=self.cover_art_var.get(),
            include_duplicates=self._last_run_checked_duplicates,
        )

    def _apply(self) -> None:
        if self.plan is None:
            messagebox.showinfo(
                "Nothing to apply",
                "Organize a library first.",
            )
            return
        if not self.selected_ids:
            messagebox.showinfo("Nothing selected", "Select at least one proposal.")
            return
        pending = batches_requiring_recovery(self.plan.root)
        if pending and not self._confirm_recovery_override(pending, self.plan.root):
            return
        if not self.plan.validate_digest():
            messagebox.showerror(
                "Plan invalid",
                "The reviewed plan no longer matches its digest. Organize again.",
            )
            return
        group_count = self._selection_group_count()
        if not messagebox.askyesno(
            "Confirm selected changes",
            f"Apply the coordinated changes for {group_count} selected song(s)?\n\n"
            "The reviewed plan will be revalidated before any file is changed.",
        ):
            return
        selected = tuple(self.selected_ids)
        plan = self.plan
        self._append_activity_log(
            f"Applying selected changes for {group_count} song(s)."
        )
        self._set_busy(True)
        self.jobs.apply(plan, selected)

    def _confirm_recovery_override(
        self,
        pending: list[dict],
        root: str,
    ) -> bool:
        root_key = path_key(root)
        if root_key in self._recovery_overrides:
            return True
        batch_word = "batch" if len(pending) == 1 else "batches"
        if not messagebox.askyesno(
            "Recovery still unresolved",
            f"{len(pending)} incomplete recovery {batch_word} for this folder "
            "still contain actions that could not be restored.\n\n"
            "You can retry 'Undo latest', or continue with the selected "
            "changes anyway. Continuing will not repair the missing file and "
            "may require manual cleanup of the older batch.\n\n"
            "Continue applying the selected changes?",
        ):
            return False
        self._recovery_overrides.add(root_key)
        self._append_activity_log(
            "Continuing despite unresolved recovery for this folder."
        )
        return True

    def _cancel(self) -> None:
        if self.jobs.active:
            self.jobs.cancel()
            self._append_activity_log("Cancellation requested.")
            self.status_var.set("Cancellation requested…")

    def _undo_latest(self) -> None:
        root = self.plan.root if self.plan is not None else self.folder_var.get().strip()
        batch = latest_undoable_batch(root or None)
        if batch is None:
            messagebox.showinfo("Nothing to undo", "No recoverable batch is available.")
            return
        if not messagebox.askyesno(
            "Undo latest batch",
            f"Restore the latest batch for {batch.get('root', 'the selected folder')}?",
        ):
            return
        self._append_activity_log("Restoring the latest batch.")
        self._set_busy(True)
        self.jobs.undo(batch["batch_id"])

    def _show_history(self) -> None:
        batches = batch_history()
        window = tk.Toplevel(self.root)
        window.title(f"{GUI_TITLE} history")
        window.geometry("760x360")
        listbox = tk.Listbox(window)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        for batch in batches:
            listbox.insert(
                tk.END,
                f"{batch.get('status', 'unknown'):18} "
                f"{_format_local_timestamp(batch.get('created_at', ''))}  "
                f"{batch.get('root', '')}",
            )
        ttk.Label(
            window,
            text="Undo latest restores completed actions from the newest "
            "completed or interrupted batch. Restore remains guarded by "
            "the batch journal.",
        ).pack(fill=tk.X, padx=10, pady=(0, 10))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "progress":
            _, stage, current, total, path = event
            progress_message = _format_progress_log(stage, current, total, path)
            self._append_activity_log(progress_message)
            self.status_var.set(progress_message)
        elif kind == "organize-complete":
            self.plan, results = event[1], event[2]
            self._set_busy(False)
            self._populate_plan(self.plan)
            self._record_applied_groups(results)
            actions = _action_items(self.plan)
            unresolved = len(self.plan.issues) + sum(
                _requires_review(item)
                for item in actions
            )
            if not actions and self.plan.issues:
                self.notebook.select(self.trees["errors"].master)
                self.status_var.set(
                    f"No proposed changes found; {len(self.plan.issues)} "
                    "file(s) failed analysis."
                )
                self._append_activity_log(
                    f"Analysis complete: no proposed changes; "
                    f"{len(self.plan.issues)} file(s) failed analysis."
                )
                messagebox.showwarning(
                    "No songs were organized",
                    "Ballad could not produce a metadata or filename change "
                    "to review. Open Skipped / errors for the causes.",
                )
                return
            recommended = len(_recommended_ids(self.plan))
            song_count = len(_grouped_action_ids(self.plan))
            duplicate_summary = (
                f"{len(self.plan.duplicate_findings)} duplicate findings."
                if self._last_run_checked_duplicates
                else "duplicate check skipped."
            )
            summary = (
                f"Analysis complete: {len(actions)} proposed change(s) across "
                f"{song_count} song(s), {recommended} high-confidence and "
                f"recommended, {unresolved} item(s) need review, "
                f"{duplicate_summary} "
                "Nothing has changed yet \u2014 select changes and click "
                "'Apply selected'."
            )
            self.status_var.set(summary)
            self._append_activity_log(summary)
        elif kind == "undo-complete":
            results = event[1]
            self._set_busy(False)
            succeeded = sum(result.status == "succeeded" for result in results)
            failures = [result for result in results if result.status == "failed"]
            summary = (
                f"Undo complete: {succeeded} restored, {len(failures)} failed."
            )
            self.status_var.set(summary)
            self._append_activity_log(summary)
            if failures:
                details = "\n".join(
                    f"\u2022 {Path(result.path).name}: {result.message}"
                    for result in failures[:10]
                )
                if len(failures) > 10:
                    details += f"\n\u2026 and {len(failures) - 10} more."
                messagebox.showwarning(
                    "Undo needs attention",
                    f"{len(failures)} action(s) could not be restored:\n\n"
                    f"{details}\n\n"
                    "This is often a transient file lock (e.g. antivirus "
                    "scanning); click 'Undo latest' again to retry.",
                )
        elif kind == "apply-complete":
            results = event[1]
            self._record_applied_groups(results)
            self._set_busy(False)
            succeeded = sum(result.status == "succeeded" for result in results)
            blocked = sum(result.status == "blocked" for result in results)
            failed = sum(result.status in {"failed", "stale"} for result in results)
            for result in results:
                if result.status in {"failed", "stale", "blocked"}:
                    self._insert_row(
                        "errors",
                        f"apply-{result.proposal_id}",
                        result.status,
                        result.path,
                        result.message,
                        "error",
                    )
            summary = (
                f"Apply complete: {succeeded} succeeded, "
                f"{blocked} blocked, {failed} failed."
            )
            self.status_var.set(summary)
            self._append_activity_log(summary)
            if failed or blocked:
                messagebox.showwarning(
                    "Apply finished with issues",
                    f"{succeeded} actions succeeded, {blocked} blocked, "
                    f"and {failed} failed. "
                    + (
                        "Blocked actions were skipped; the successful actions "
                        "do not need to be undone. "
                        if blocked and not failed
                        else "Use Undo latest to restore successful actions "
                        "when a mutation failed. "
                    )
                    + "Open the error tab for details.",
                )
        elif kind == "failed":
            self._set_busy(False)
            self.status_var.set("Operation failed.")
            self._append_activity_log(f"Operation failed: {event[1]}")
            messagebox.showerror("Operation failed", event[1])

    def _clear_trees(self) -> None:
        tree_headings = getattr(self, "_tree_headings", {})
        for tree_name, tree in self.trees.items():
            tree.delete(*tree.get_children())
            for column, label in tree_headings.get(tree_name, {}).items():
                tree.heading(column, text=label)
        self._row_ids.clear()
        self._row_paths.clear()
        getattr(self, "_sort_state", {}).clear()
        getattr(self, "_selection_anchors", {}).clear()

    def _sort_tree_column(self, tree_name: str, column: str) -> None:
        """Reorder one tree's rows by a column's text; repeat clicks reverse it.

        Uses `move` rather than delete-and-reinsert so `_row_ids`/`_row_paths`
        (keyed by item id) stay valid without needing to be rebuilt.
        """
        tree = self.trees[tree_name]
        reverse = self._sort_state.get((tree_name, column), False)
        children = sorted(
            tree.get_children(""),
            key=lambda iid: tree.set(iid, column).casefold(),
            reverse=reverse,
        )
        for index, iid in enumerate(children):
            tree.move(iid, "", index)
        self._sort_state[(tree_name, column)] = not reverse
        arrow = "\u25bc" if reverse else "\u25b2"
        for other_column, label in self._tree_headings.get(tree_name, {}).items():
            text = f"{label} {arrow}" if other_column == column else label
            tree.heading(other_column, text=text)

    def _populate_plan(self, plan: ReviewPlan) -> None:
        self._clear_trees()
        for row in plan_rows(plan):
            if row.is_change:
                self._insert_change_row(
                    row.tree,
                    row.item_id,
                    row.action,
                    row.path,
                    row.current,
                    row.proposed,
                    row.confidence,
                )
            else:
                self._insert_row(
                    row.tree,
                    row.item_id,
                    row.action,
                    row.path,
                    row.current,
                    row.confidence,
                )

    def _insert_duplicate_finding(self, item) -> None:
        paths = item.paths or ("",)
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            self._insert_row(
                "duplicates",
                f"{item.id}:{index}",
                f"{item.classification} ({index}/{total})",
                path,
                item.recommendation,
                item.confidence,
            )

    def _insert_row(
        self,
        tree_name: str,
        item_id: str,
        action: str,
        path: str,
        summary: str,
        confidence: str,
    ) -> None:
        tree = self.trees[tree_name]
        display_file = Path(path).name if path else ""
        row = tree.insert(
            "",
            tk.END,
            values=(action, display_file, summary, confidence),
            tags=_confidence_row_tags(confidence),
        )
        self._row_ids[(tree_name, row)] = item_id
        self._row_paths[(tree_name, row)] = path

    def _insert_change_row(
        self,
        tree_name: str,
        item_id: str,
        action: str,
        path: str,
        current: str,
        proposed: str,
        confidence: str,
    ) -> None:
        tree = self.trees[tree_name]
        values = ["☐", action]
        if tree_name == "tags":
            values.append(Path(path).name if path else "")
        values.extend((current, proposed, confidence))
        row = tree.insert(
            "",
            tk.END,
            values=values,
            tags=_confidence_row_tags(confidence),
        )
        self._row_ids[(tree_name, row)] = item_id
        self._row_paths[(tree_name, row)] = path

    def _handle_tree_context_menu(self, tree_name: str, event):
        tree = self.trees[tree_name]
        row = tree.identify_row(event.y)
        if not row:
            return "break"
        if row not in tree.selection():
            tree.selection_set(row)
        path = self._row_paths.get((tree_name, row))
        if not path:
            return "break"

        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(
            label="Play",
            command=lambda: self._open_with_default_app(path),
        )
        menu.add_command(
            label="Open in File Explorer",
            command=lambda: self._open_in_file_explorer(path),
        )
        row_ids = getattr(self, "_row_ids", {})
        proposal = self._proposal_for_id(row_ids.get((tree_name, row), ""))
        if tree_name == "tags" and proposal is not None and proposal.evidence:
            menu.add_separator()
            menu.add_command(
                label="Show metadata evidence",
                command=lambda: self._show_metadata_evidence(proposal),
            )
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _show_metadata_evidence(self, proposal) -> None:
        """Show the source IDs used to prepare an enrichment proposal."""
        messagebox.showinfo(
            "Metadata evidence",
            json.dumps(proposal.evidence.to_dict(), indent=2, ensure_ascii=False),
        )

    def _open_in_file_explorer(self, path: str) -> None:
        target = Path(path)
        if not target.is_file():
            messagebox.showwarning(
                "File unavailable",
                f"This file is no longer available:\n{target}",
            )
            return
        try:
            options = {}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
                subprocess.Popen(
                    ["explorer.exe", "/select,", str(target)],
                    **options,
                )
            else:
                subprocess.Popen(["xdg-open", str(target.parent)], **options)
        except OSError as exc:
            messagebox.showerror(
                "Could not open File Explorer",
                str(exc),
            )

    def _open_with_default_app(self, path: str) -> None:
        """Launch a row's audio file in whatever program handles it by default.

        This mirrors double-clicking the file in a file manager. Bound to
        double-click rather than single-click because single-click already
        toggles the row's checkbox; firing a media player on every click
        while multi-selecting rows to review would be disruptive.
        """
        target = Path(path)
        if not target.is_file():
            messagebox.showwarning(
                "File unavailable",
                f"This file is no longer available:\n{target}",
            )
            return
        try:
            if os.name == "nt":
                os.startfile(str(target))  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            messagebox.showerror("Could not open file", str(exc))

    def _proposal_for_id(self, item_id: str):
        plan = getattr(self, "plan", None)
        if plan is None:
            return None
        return next(
            (item for item in _action_items(plan) if item.id == item_id),
            None,
        )

    def _record_applied_groups(self, results) -> None:
        if self.plan is None:
            return
        successful_ids = {
            result.proposal_id
            for result in results
            if result.status == "succeeded"
        }
        self._applied_group_ids.update(
            item.decision_group_id
            for item in _action_items(self.plan)
            if item.id in successful_ids
        )
        self._set_selected_ids(self.selected_ids)

    def _group_was_applied(self, proposal) -> bool:
        return proposal.decision_group_id in getattr(
            self,
            "_applied_group_ids",
            set(),
        )

    def _selection_group_count(self) -> int:
        plan = getattr(self, "plan", None)
        if plan is None:
            return len(self.selected_ids)
        groups = _grouped_action_ids(plan)
        return sum(bool(self.selected_ids & item_ids) for item_ids in groups.values())

    def _update_primary_button(self) -> None:
        """Keep the one prominent call-to-action mapped to the current step.

        Before a plan exists, this button *is* "Organize library" rather
        than a separate, easy-to-miss button elsewhere. Once a plan is
        loaded, the same button becomes "Apply selected", so there's always
        exactly one obvious next action instead of splitting attention
        between a subdued button and a prominent one that starts out inert.
        """
        button = getattr(self, "primary_button", None)
        if button is None:
            return
        if self.plan is None:
            button.configure(
                text="Organize library",
                command=self._organize_library,
                state=tk.NORMAL,
            )
            return
        group_count = self._selection_group_count()
        button.configure(
            text=(
                f"Apply selected ({group_count})"
                if group_count
                else "Apply selected"
            ),
            command=self._apply,
            state=tk.NORMAL if group_count else tk.DISABLED,
        )

    def _handle_tree_double_click(self, tree_name: str, event):
        tree = self.trees[tree_name]
        if tree.identify_region(event.x, event.y) != "cell":
            return None
        # The first column is the checkbox. Double-clicking it means "I
        # clicked the box twice", not "play this"; launching a media player
        # from the same target the user aims at to select rows is jarring.
        if tree.identify_column(event.x) == "#1" and tree_name in {"renames", "tags"}:
            return "break"
        row = tree.identify_row(event.y)
        if not row:
            return None
        path = self._row_paths.get((tree_name, row))
        if path:
            self._open_with_default_app(path)
        return None

    def _handle_tree_click(self, tree_name: str, event):
        tree = self.trees[tree_name]
        self._selection_anchors = getattr(self, "_selection_anchors", {})
        if tree.identify_region(event.x, event.y) == "separator":
            column = tree.identify_column(event.x)
            index = int(column[1:]) - 1 if column.startswith("#") else -1
            adjacent = {column}
            if index > 0:
                adjacent.add(f"#{index}")
            tree_columns = tree["columns"]
            if any(
                tree_columns[int(name[1:]) - 1] in _FIXED_TREE_COLUMNS
                for name in adjacent
                if name.startswith("#")
                and int(name[1:]) <= len(tree_columns)
            ):
                return "break"
        if tree_name not in {"renames", "tags"}:
            return None
        column = tree.identify_column(event.x)
        row = tree.identify_row(event.y)
        if not row:
            return None if column != "#1" else "break"
        shift_pressed = bool(getattr(event, "state", 0) & _SHIFT_MASK)
        if column != "#1":
            if not shift_pressed:
                self._selection_anchors[tree_name] = row
            elif tree_name not in self._selection_anchors:
                self._selection_anchors[tree_name] = row
            return None

        rows = list(tree.selection())
        if shift_pressed:
            anchor = self._selection_anchors.get(tree_name)
            visible_rows = list(tree.get_children(""))
            if anchor in visible_rows and row in visible_rows:
                start = visible_rows.index(anchor)
                end = visible_rows.index(row)
                rows = visible_rows[min(start, end) : max(start, end) + 1]
                tree.selection_set(rows)
        else:
            self._selection_anchors[tree_name] = row
        if row not in rows:
            tree.selection_set(row)
            rows = [row]
        item_ids = {
            item_id
            for selected_row in rows
            if (item_id := self._row_ids.get((tree_name, selected_row)))
        }
        clicked_id = self._row_ids.get((tree_name, row))
        if not clicked_id or not item_ids:
            return "break"
        clicked = self._proposal_for_id(clicked_id)
        if clicked is None:
            if clicked_id in self.selected_ids:
                self._set_selected_ids(self.selected_ids - item_ids)
            else:
                self._set_selected_ids(self.selected_ids | item_ids)
            return "break"
        if self._group_was_applied(clicked):
            self.status_var.set(
                "This song was already changed in this run. Organize again "
                "before making more changes."
            )
            return "break"
        if not clicked.apply_eligible:
            self.status_var.set(
                "This song has a blocking issue and cannot be selected until "
                "it is resolved."
            )
            return "break"
        groups = _grouped_action_ids(self.plan)
        selected_groups = {
            proposal.decision_group_id
            for item_id in item_ids
            if (proposal := self._proposal_for_id(item_id)) is not None
            and proposal.apply_eligible
            and not self._group_was_applied(proposal)
        }
        grouped_ids = {
            item_id
            for group_id in selected_groups
            for item_id in groups[group_id]
        }
        if clicked_id in self.selected_ids:
            self._set_selected_ids(self.selected_ids - grouped_ids)
        else:
            self._set_selected_ids(self.selected_ids | grouped_ids)
        return "break"

    def _select_recommended(self) -> None:
        if self.plan is None:
            return
        recommended = _recommended_ids(self.plan)
        self._set_selected_ids(recommended)
        self.status_var.set(
            f"Selected {self._selection_group_count()} recommended songs."
        )

    def _select_all(self) -> None:
        if self.plan is None:
            return
        selected = _ready_ids(self.plan)
        self._set_selected_ids(selected)
        skipped = len(_grouped_action_ids(self.plan)) - self._selection_group_count()
        self.status_var.set(
            f"Selected {self._selection_group_count()} ready songs; "
            f"{skipped} need review."
        )

    def _edit_selected_filename(self) -> None:
        if self.plan is None:
            messagebox.showinfo(
                "Nothing to edit",
                "Organize a library first.",
            )
            return
        tree = self.trees["renames"]
        rows = tree.selection()
        if len(rows) != 1:
            messagebox.showinfo(
                "Choose a rename",
                "Click one row in Proposed renames, then choose Edit filename.",
            )
            return
        row = rows[0]
        item_id = self._row_ids.get(("renames", row))
        proposal = next(
            (
                item
                for item in self.plan.rename_proposals
                if item.id == item_id
            ),
            None,
        )
        if proposal is None:
            messagebox.showerror("Rename unavailable", "That rename is no longer available.")
            return
        if self._group_was_applied(proposal):
            messagebox.showinfo(
                "Rename already applied",
                "Organize the library again before editing this song.",
            )
            return

        filename = _ask_filename(
            self.root,
            proposal.proposed_values.get("filename", ""),
        )
        if filename is None:
            return
        filename = filename.strip()
        error = _filename_validation_error(filename, proposal.old_path)
        if error:
            messagebox.showerror("Invalid filename", error)
            return
        new_path = canonical_path(str(Path(proposal.old_path).with_name(filename)))
        if path_key(new_path) == path_key(proposal.old_path):
            messagebox.showerror(
                "No change",
                "The corrected filename must differ from the current filename.",
            )
            return
        if any(
            item.id != proposal.id and path_key(item.new_path) == path_key(new_path)
            for item in self.plan.rename_proposals
        ):
            messagebox.showerror(
                "Filename already proposed",
                "Another reviewed rename already uses that filename.",
            )
            return

        new_id = proposal_id("rename", proposal.old_path, new_path)
        updated = replace(
            proposal,
            id=new_id,
            new_path=new_path,
            proposed_values={
                **proposal.proposed_values,
                "filename": filename,
            },
            reason=f"{proposal.reason} Filename corrected during review.",
        )
        proposals = tuple(
            updated if item.id == proposal.id else item
            for item in self.plan.rename_proposals
        )
        proposals = refresh_rename_readiness(proposals)
        tags, _, _ = coordinate_tag_proposals(
            proposals,
            list(self.plan.tag_proposals),
        )
        was_selected = proposal.id in self.selected_ids
        self.plan = self.plan.with_proposals(proposals, tags)
        self._populate_plan(self.plan)
        if was_selected:
            group_ids = _grouped_action_ids(self.plan).get(
                updated.decision_group_id,
                set(),
            )
            self._set_selected_ids(group_ids)
        else:
            self._set_selected_ids(self.selected_ids - {proposal.id})
        self.status_var.set(f"Corrected proposed filename to {filename}.")

    def _set_selected_ids(self, selected_ids) -> None:
        plan = getattr(self, "plan", None)
        selected = (
            _expand_group_selection(
                plan,
                selected_ids,
                include_review=True,
            )
            if plan is not None
            else set(selected_ids)
        )
        if plan is not None:
            groups = _grouped_action_ids(plan)
            selected = {
                item_id
                for group_id, item_ids in groups.items()
                if group_id not in getattr(self, "_applied_group_ids", set())
                for item_id in item_ids
                if item_id in selected
            }
        self.selected_ids = selected
        for tree_name in ("renames", "tags"):
            tree = self.trees[tree_name]
            for row in tree.get_children(""):
                values = list(tree.item(row, "values"))
                if values:
                    values[0] = (
                        "☑"
                        if self._row_ids.get((tree_name, row))
                        in self.selected_ids
                        else "☐"
                    )
                    tree.item(row, values=values)
        self._update_primary_button()

    def _close(self) -> None:
        if self.jobs.active:
            if not messagebox.askyesno(
                "Cancel operation", "Request cancellation and close the application?"
            ):
                return
            self.jobs.cancel()
            self.root.after(100, self._close)
            return
        self._release_windows_icon_handles()
        self.root.destroy()

    def _release_windows_icon_handles(self) -> None:
        if os.name != "nt" or not self._icon_handles:
            return
        try:
            import ctypes

            destroy_icon = ctypes.windll.user32.DestroyIcon
            destroy_icon.argtypes = [ctypes.c_void_p]
            for handle in self._icon_handles:
                destroy_icon(handle)
        except (AttributeError, OSError, TypeError):
            pass
        self._icon_handles = ()


def run() -> None:
    ensure_app_dirs()
    _set_windows_app_identity()
    root = tk.Tk()
    SongOrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
