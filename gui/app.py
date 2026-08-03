"""Review-first tkinter application."""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import subprocess
from dataclasses import replace
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from PIL import Image, ImageTk

from renamer.media import read_front_artwork

from renamer.domain.issues import ReviewIssue
from renamer.quarantine import (
    load_quarantine,
    quarantine_file,
    unquarantine_files,
)
from renamer.apply import (
    batch_history,
    batches_requiring_recovery,
    latest_undoable_batch,
    undo_batch,
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
    artwork_ids as _artwork_ids,
    expand_group_selection as _expand_group_selection,
    grouped_action_ids as _grouped_action_ids,
    is_high_confidence_action as _is_high_confidence_action,
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


def _shared_folder_artwork(folder: str) -> tuple[Path, ...]:
    """Find player-wide fallback images directly inside a selected folder."""
    candidates = []
    for path in Path(folder).iterdir():
        name = path.name.casefold()
        generated_album_art = (
            name.startswith("albumart_") and path.suffix.casefold() in _IMAGE_EXTENSIONS
        )
        if path.is_file() and (
            name in _SHARED_ARTWORK_NAMES or generated_album_art
        ):
            candidates.append(path)
    return tuple(sorted(candidates, key=lambda path: path.name.casefold()))


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
        self.propose_renames_var = tk.BooleanVar(value=False)
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
        self.propose_renames_check = ttk.Checkbutton(
            options,
            text="Propose filename changes",
            variable=self.propose_renames_var,
        )
        self.propose_renames_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.propose_renames_check,
            "Suggest renaming files on disk based on enriched metadata. "
            "When unchecked, Ballad enriches tags and embeds cover art without changing filenames.",
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
        self.tabs: dict[str, ttk.Frame] = {}
        for key, title in (
            ("renames", "Filename changes"),
            ("tags", "Metadata changes"),
            ("duplicates", "Duplicate findings (read-only)"),
            ("errors", "Skipped / errors"),
        ):
            frame = ttk.Frame(self.notebook, padding=6)
            self.notebook.add(frame, text=title)
            self.tabs[key] = frame
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
        ttk.Button(
            selection_controls,
            text="Select missing artwork",
            command=self._select_artwork,
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
        self.quarantine_button = ttk.Button(
            secondary_actions, text="Quarantine", command=self._handle_quarantine_button_click
        )
        self.quarantine_button.pack(side=tk.LEFT, padx=(8, 0))
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
        self._preview_images: list[Any] = []
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

        self.sidebar_panel = ttk.Frame(self.activity_container)
        self.sidebar_panel.grid(row=0, column=0, sticky=tk.NSEW)
        self.sidebar_panel.columnconfigure(0, weight=1)
        self.sidebar_panel.rowconfigure(0, weight=1)

        self.sidebar_notebook = ttk.Notebook(self.sidebar_panel)
        self.sidebar_notebook.grid(row=0, column=0, sticky=tk.NSEW)

        # Tab 1: Activity
        self.activity_tab = ttk.Frame(self.sidebar_notebook, padding=6)
        self.sidebar_notebook.add(self.activity_tab, text="Activity")

        activity_header = ttk.Frame(self.activity_tab)
        activity_header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(
            activity_header,
            text="Live progress",
            font=("TkDefaultFont", 9, "bold"),
        ).pack(side=tk.LEFT)
        collapse_btn1 = ttk.Button(
            activity_header,
            text="Collapse",
            command=self._toggle_activity_sidebar,
        )
        collapse_btn1.pack(side=tk.RIGHT)
        _add_tooltip(collapse_btn1, "Collapse the activity sidebar.")

        log_frame = ttk.Frame(self.activity_tab)
        log_frame.pack(fill=tk.BOTH, expand=True)
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

        # Tab 2: Review details
        self.details_tab = ttk.Frame(self.sidebar_notebook, padding=6)
        self.sidebar_notebook.add(self.details_tab, text="Review details")

        details_header = ttk.Frame(self.details_tab)
        details_header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(
            details_header,
            text="Proposal Inspector",
            font=("TkDefaultFont", 9, "bold"),
        ).pack(side=tk.LEFT)
        collapse_btn2 = ttk.Button(
            details_header,
            text="Collapse",
            command=self._toggle_activity_sidebar,
        )
        collapse_btn2.pack(side=tk.RIGHT)
        _add_tooltip(collapse_btn2, "Collapse the activity sidebar.")

        details_canvas_frame = ttk.Frame(self.details_tab)
        details_canvas_frame.pack(fill=tk.BOTH, expand=True)
        details_canvas_frame.columnconfigure(0, weight=1)
        details_canvas_frame.rowconfigure(0, weight=1)

        self.details_canvas = tk.Canvas(details_canvas_frame, highlightthickness=0)
        self.details_scrollbar = ttk.Scrollbar(
            details_canvas_frame,
            orient=tk.VERTICAL,
            command=self.details_canvas.yview,
        )
        self.details_content = ttk.Frame(self.details_canvas)
        self.details_content.bind(
            "<Configure>",
            lambda e: self.details_canvas.configure(
                scrollregion=self.details_canvas.bbox("all")
            ),
        )
        self.details_canvas_window = self.details_canvas.create_window(
            (0, 0), window=self.details_content, anchor="nw"
        )
        self.details_canvas.configure(yscrollcommand=self.details_scrollbar.set)
        self.details_canvas.bind(
            "<Configure>",
            lambda e: self.details_canvas.itemconfig(
                self.details_canvas_window, width=e.width
            ),
        )
        self.details_canvas.grid(row=0, column=0, sticky=tk.NSEW)
        self.details_scrollbar.grid(row=0, column=1, sticky=tk.NS)

        def _on_mousewheel(event):
            self.details_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.details_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self.activity_show_button = ttk.Button(
            self.activity_container,
            text="Show sidebar",
            command=self._toggle_activity_sidebar,
            padding=(2, 2),
        )
        _add_tooltip(
            self.activity_show_button,
            "Show the live activity & review sidebar.",
        )

        self._update_review_details(None)

    def _toggle_activity_sidebar(self) -> None:
        if self._activity_sidebar_open:
            self.sidebar_panel.grid_remove()
            self.activity_show_button.grid(
                row=0,
                column=0,
                sticky=tk.N,
                pady=(8, 0),
            )
            self.activity_container.configure(width=_ACTIVITY_COLLAPSED_WIDTH)
        else:
            self.activity_show_button.grid_remove()
            self.sidebar_panel.grid(row=0, column=0, sticky=tk.NSEW)
            self.activity_container.configure(width=_ACTIVITY_SIDEBAR_WIDTH)
        self._activity_sidebar_open = not self._activity_sidebar_open

    def _on_tree_select(self, tree_name: str, event=None) -> None:
        tree = self.trees.get(tree_name)
        if not tree:
            return
        selection = tree.selection()
        if not selection:
            return
        row = selection[0]
        item_id = self._row_ids.get((tree_name, row))
        if not item_id:
            return
        proposal = self._proposal_for_id(item_id)
        if proposal is not None:
            self._update_review_details(proposal)

    def _load_tk_image_bytes(self, data: bytes, max_size=(110, 110)):
        try:
            img = Image.open(io.BytesIO(data))
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _load_tk_image_file(self, file_path: str, max_size=(110, 110)):
        try:
            img = Image.open(file_path)
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _update_review_details(self, proposal: Any) -> None:
        for child in self.details_content.winfo_children():
            child.destroy()
        self._preview_images.clear()

        if proposal is None:
            lbl = ttk.Label(
                self.details_content,
                text="Select a song proposal in the table to inspect local tags, MusicBrainz evidence, warnings, and artwork.",
                wraplength=320,
                justify=tk.LEFT,
                padding=10,
            )
            lbl.pack(fill=tk.X)
            return

        container = ttk.Frame(self.details_content, padding=4)
        container.pack(fill=tk.BOTH, expand=True)

        file_path = getattr(proposal, "path", None) or getattr(proposal, "old_path", None) or ""
        file_name = Path(file_path).name if file_path else "Unknown file"

        lbl_title = ttk.Label(
            container,
            text=file_name,
            font=("TkDefaultFont", 10, "bold"),
            wraplength=320,
        )
        lbl_title.pack(anchor=tk.W, pady=(0, 2))

        conf = str(getattr(proposal, "confidence", "medium")).upper()
        status_text = f"Confidence: {conf}"
        conf_color = {
            "HIGH": "green",
            "MEDIUM": "#b8860b",
            "LOW": "red",
            "BLOCKING": "red",
        }.get(conf, "black")

        conf_frame = ttk.Frame(container)
        conf_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            conf_frame,
            text=status_text,
            foreground=conf_color,
            font=("TkDefaultFont", 9, "bold"),
        ).pack(side=tk.LEFT)

        issues = getattr(proposal, "review_issues", ()) or ()
        warnings = getattr(proposal, "warnings", ()) or ()
        if issues or warnings:
            warn_box = ttk.Labelframe(container, text="Warnings & Issues", padding=6)
            warn_box.pack(fill=tk.X, pady=(0, 8))
            for issue in issues:
                msg = getattr(issue, "message", str(issue))
                lbl_warn = ttk.Label(
                    warn_box,
                    text=f"• {msg}",
                    foreground="red" if not getattr(issue, "apply_eligible", True) else "#d9534f",
                    wraplength=300,
                    justify=tk.LEFT,
                )
                lbl_warn.pack(anchor=tk.W, pady=1)
            for w in warnings:
                if not any(w in getattr(i, "message", "") for i in issues):
                    lbl_w = ttk.Label(
                        warn_box,
                        text=f"• {w}",
                        foreground="#d9534f",
                        wraplength=300,
                        justify=tk.LEFT,
                    )
                    lbl_w.pack(anchor=tk.W, pady=1)

        meta_box = ttk.Labelframe(container, text="Metadata Proposal", padding=6)
        meta_box.pack(fill=tk.X, pady=(0, 8))

        if hasattr(proposal, "before") and hasattr(proposal, "after"):
            before = proposal.before
            after = proposal.after
            fields = [
                ("Artist", before.get("artist"), after.get("artist")),
                ("Title", before.get("title"), after.get("title")),
                ("Album", before.get("album"), after.get("album")),
                ("Track", before.get("track_number"), after.get("track_number")),
            ]
            for label, b_val, a_val in fields:
                if b_val or a_val:
                    row = ttk.Frame(meta_box)
                    row.pack(fill=tk.X, pady=2)
                    ttk.Label(row, text=f"{label}:", font=("TkDefaultFont", 9, "bold"), width=8).pack(side=tk.LEFT)
                    val_str = f"{b_val or '(none)'}  ➔  {a_val or '(none)'}" if b_val != a_val else f"{a_val or '(unchanged)'}"
                    ttk.Label(row, text=val_str, wraplength=230, justify=tk.LEFT).pack(side=tk.LEFT)

        if hasattr(proposal, "old_path") and hasattr(proposal, "new_path"):
            old_name = Path(proposal.old_path).name
            new_name = Path(proposal.new_path).name
            row = ttk.Frame(meta_box)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text="Filename:", font=("TkDefaultFont", 9, "bold"), width=8).pack(side=tk.LEFT)
            val_str = f"{old_name}\n➔ {new_name}" if old_name != new_name else f"{new_name}"
            ttk.Label(row, text=val_str, wraplength=230, justify=tk.LEFT).pack(side=tk.LEFT)

        ev = getattr(proposal, "evidence", None)
        ev_dict = ev.to_dict() if hasattr(ev, "to_dict") else (ev if isinstance(ev, dict) else {})
        ident = ev_dict.get("identification") or {}
        mb = ev_dict.get("musicbrainz") or {}

        if ident or mb:
            ev_box = ttk.Labelframe(container, text="Provider Evidence", padding=6)
            ev_box.pack(fill=tk.X, pady=(0, 8))

            if ident.get("score"):
                score = ident.get("score")
                score_str = f"{int(score * 100)}%" if isinstance(score, float) and score <= 1.0 else f"{score}%" if isinstance(score, (int, float)) else str(score)
                ttk.Label(ev_box, text=f"AcoustID Match: {score_str}", font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W)

            if mb.get("recording_id"):
                ttk.Label(ev_box, text=f"MB Recording ID:\n{mb.get('recording_id')}", font=("TkDefaultFont", 8)).pack(anchor=tk.W, pady=(2, 0))
            if mb.get("release_id"):
                ttk.Label(ev_box, text=f"MB Release ID:\n{mb.get('release_id')}", font=("TkDefaultFont", 8)).pack(anchor=tk.W, pady=(2, 0))
            if mb.get("release"):
                ttk.Label(ev_box, text=f"Release: {mb.get('release')}", wraplength=300).pack(anchor=tk.W, pady=(2, 0))
            if mb.get("date"):
                ttk.Label(ev_box, text=f"Release Date: {mb.get('date')}").pack(anchor=tk.W, pady=(2, 0))
        else:
            ev_box = ttk.Labelframe(container, text="Provider Evidence", padding=6)
            ev_box.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(ev_box, text="No online provider evidence attached.", foreground="gray").pack(anchor=tk.W)

        art_box = ttk.Labelframe(container, text="Cover Art Inspection", padding=6)
        art_box.pack(fill=tk.X, pady=(0, 8))

        art_row = ttk.Frame(art_box)
        art_row.pack(fill=tk.X)

        curr_frame = ttk.Frame(art_row)
        curr_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=2)
        ttk.Label(curr_frame, text="Current Embedded", font=("TkDefaultFont", 8, "bold")).pack(anchor=tk.N)

        current_art = (
            read_front_artwork(file_path)
            if file_path and os.path.isfile(file_path)
            else None
        )
        current_art_bytes, _ = current_art or (None, "")
        if current_art_bytes:
            curr_sha = hashlib.sha256(current_art_bytes).hexdigest()
            art_before = getattr(proposal, "artwork_before", None)
            stale = False
            if art_before and getattr(art_before, "sha256", "") and art_before.sha256 != curr_sha:
                stale = True

            tk_img = self._load_tk_image_bytes(current_art_bytes)
            if tk_img:
                self._preview_images.append(tk_img)
                lbl_img = ttk.Label(curr_frame, image=tk_img)
                lbl_img.pack(pady=4)
                status_lbl = "Embedded (Stale)" if stale else "Embedded Cover"
                color = "#d9534f" if stale else "gray"
                ttk.Label(curr_frame, text=status_lbl, font=("TkDefaultFont", 7), foreground=color).pack(anchor=tk.N)
            else:
                ttk.Label(curr_frame, text="[Image decode error]", foreground="red").pack(pady=10)
        else:
            ttk.Label(curr_frame, text="[No cover art]", foreground="gray").pack(pady=20)

        staged_frame = ttk.Frame(art_row)
        staged_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=2)
        ttk.Label(staged_frame, text="Proposed Replacement", font=("TkDefaultFont", 8, "bold")).pack(anchor=tk.N)

        art_after = getattr(proposal, "artwork_after", None)
        staged_path = getattr(art_after, "path", None) if art_after else None
        if staged_path and os.path.isfile(staged_path):
            tk_staged = self._load_tk_image_file(staged_path)
            if tk_staged:
                self._preview_images.append(tk_staged)
                lbl_staged = ttk.Label(staged_frame, image=tk_staged)
                lbl_staged.pack(pady=4)
                rel_id = getattr(art_after, "release_id", "")
                src_url = getattr(art_after, "source_url", "")
                sub_txt = f"Release: {rel_id[:8]}..." if rel_id else ("CAA Source" if src_url else "Proposed")
                ttk.Label(staged_frame, text=sub_txt, font=("TkDefaultFont", 7), foreground="green").pack(anchor=tk.N)
            else:
                ttk.Label(staged_frame, text="[Decode error]", foreground="red").pack(pady=10)
        else:
            ttk.Label(staged_frame, text="[No change]", foreground="gray").pack(pady=20)

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
            "<<TreeviewSelect>>",
            lambda event, name=key: self._on_tree_select(name, event),
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

    def _resolve_shared_folder_artwork(
        self,
        folder: str,
    ) -> tuple[Path, ...] | None:
        artwork = _shared_folder_artwork(folder)
        if not artwork:
            return ()
        preview = artwork[:_SHARED_ARTWORK_PREVIEW_LIMIT]
        names = "\n".join(f"• {path.name}" for path in preview)
        remaining = len(artwork) - len(preview)
        if remaining:
            names += f"\n• …and {remaining} more"
        remove = messagebox.askyesnocancel(
            "Shared folder artwork detected",
            f"Ballad found {len(artwork)} artwork file(s) that media players "
            "can display for "
            "every song in this folder when a track has no embedded cover:\n\n"
            f"{names}\n\n"
            "Remove these shared images before analysis?\n\n"
            "Yes: permanently delete them and continue\n"
            "No: keep them and continue\n"
            "Cancel: stop",
        )
        if remove is None:
            return None
        if not remove:
            return ()
        removed = []
        try:
            for path in artwork:
                path.unlink()
                removed.append(path)
        except OSError as exc:
            messagebox.showerror(
                "Could not remove shared artwork",
                f"Ballad could not remove {path.name}:\n\n{exc}",
            )
            return None
        return tuple(removed)

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
        self.propose_renames_check.configure(state=state)
        self.duplicate_check_check.configure(state=state)
        self._sync_fingerprint_availability(busy=busy)
        self.edit_button.configure(state=state)
        self.cancel_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        self.history_button.configure(state=state)
        self.quarantine_button.configure(state=state)
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
        removed_artwork = self._resolve_shared_folder_artwork(folder)
        if removed_artwork is None:
            return
        self.plan = None
        self.selected_ids.clear()
        self._applied_group_ids = set()
        self._clear_trees()
        self._clear_activity_log()
        if removed_artwork:
            self._append_activity_log(
                "Removed shared folder artwork: "
                + ", ".join(path.name for path in removed_artwork)
            )
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
            include_renames=self.propose_renames_var.get(),
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

    def _selected_proposals_in_active_tree(self) -> list[Any]:
        active_tab_key = self._active_action_scope()
        if active_tab_key not in {"renames", "tags"}:
            active_tab_key = "renames" if self.plan and self.plan.rename_proposals else "tags"
        tree = self.trees.get(active_tab_key)
        proposals = []
        seen_ids = set()
        row_ids = getattr(self, "_row_ids", {})
        if tree:
            selected_rows = tree.selection()
            for row in selected_rows:
                item_id = row_ids.get((active_tab_key, row))
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    p = self._proposal_for_id(item_id)
                    if p is not None:
                        proposals.append(p)
        if not proposals and self.plan is not None:
            for item in _action_items(self.plan):
                if item.id in self.selected_ids and item.id not in seen_ids:
                    seen_ids.add(item.id)
                    proposals.append(item)
        return proposals

    def _quarantine_proposals(self, proposals: list[Any]) -> None:
        if not proposals:
            return

        valid_proposals = []
        seen_paths = set()
        for p in proposals:
            target_path = getattr(p, "path", None) or getattr(p, "old_path", None)
            if target_path and target_path not in seen_paths:
                seen_paths.add(target_path)
                valid_proposals.append((p, target_path))

        if not valid_proposals:
            return

        count = len(valid_proposals)
        if count == 1:
            p, target_path = valid_proposals[0]
            file_name = Path(target_path).name
            confirm_msg = (
                f"Ignore future online matches for '{file_name}'?\n\n"
                "Ballad will save this choice and skip online identification for this file on future runs. "
                "You can clear this anytime in Quarantine manager."
            )
        else:
            confirm_msg = (
                f"Ignore future online matches for {count} selected files?\n\n"
                "Ballad will save these choices and skip online identification for these files on future runs. "
                "You can clear these anytime in Quarantine manager."
            )

        if not messagebox.askyesno("Ignore matches in future", confirm_msg):
            return

        quarantined_count = 0
        quarantined_group_ids = set()
        new_issues = list(self.plan.issues) if self.plan is not None else []

        for p, target_path in valid_proposals:
            artist = ""
            title = ""
            if hasattr(p, "after"):
                artist = str(p.after.get("artist") or "")
                title = str(p.after.get("title") or "")
            elif hasattr(p, "proposed_values"):
                artist = str(p.proposed_values.get("artist") or "")
                title = str(p.proposed_values.get("title") or "")

            quarantine_file(
                target_path,
                artist=artist,
                title=title,
                reason="Ignored by user during review",
            )
            quarantined_count += 1
            quarantined_group_ids.add(p.decision_group_id)

            new_issues.append(
                ReviewIssue.from_dict({
                    "category": "quarantined",
                    "path": target_path,
                    "message": "Match ignored by user quarantine.",
                })
            )

        if self.plan is not None:
            new_renames = [
                r for r in self.plan.rename_proposals
                if r.decision_group_id not in quarantined_group_ids
            ]
            new_tags = [
                t for t in self.plan.tag_proposals
                if t.decision_group_id not in quarantined_group_ids
            ]

            quarantined_action_ids = set()
            grouped_actions = _grouped_action_ids(self.plan)
            for gid in quarantined_group_ids:
                quarantined_action_ids.update(grouped_actions.get(gid, set()))

            self.selected_ids -= quarantined_action_ids
            self.plan = self.plan.with_proposals(new_renames, new_tags)
            self.plan = replace(self.plan, issues=tuple(new_issues), digest="")
            self.plan = replace(self.plan, digest=self.plan._computed_digest())
            self._populate_plan(self.plan)
            self._update_primary_button()

        self.status_var.set(f"Added {quarantined_count} file(s) to quarantine.")

    def _handle_quarantine_button_click(self) -> None:
        proposals = self._selected_proposals_in_active_tree()
        if proposals:
            self._quarantine_proposals(proposals)
        else:
            self._show_quarantine_manager()

    def _show_quarantine_manager(self) -> None:
        window = tk.Toplevel(self.root)
        window.title(f"{GUI_TITLE} quarantine")
        window.geometry("780x380")

        tree_frame = ttk.Frame(window, padding=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("file", "artist_title", "date")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        tree.heading("file", text="File")
        tree.heading("artist_title", text="Ignored Match")
        tree.heading("date", text="Date")
        tree.column("file", width=320)
        tree.column("artist_title", width=280)
        tree.column("date", width=140)

        def populate_tree():
            tree.delete(*tree.get_children())
            current_items = load_quarantine()
            for item in current_items:
                f_name = Path(item.get("path", "")).name
                artist_title = " / ".join(filter(None, [item.get("artist"), item.get("title")])) or "Ignored"
                dt = _format_local_timestamp(item.get("created_at", ""))
                tree.insert("", tk.END, iid=path_key(item.get("path", "")), values=(f_name, artist_title, dt))

        populate_tree()

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)

        bottom = ttk.Frame(window, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)

        def remove_selected():
            selected = tree.selection()
            if not selected:
                return
            removed = unquarantine_files(list(selected))
            if removed > 0:
                populate_tree()
                self.status_var.set(f"Removed {removed} file(s) from quarantine.")

        ttk.Button(bottom, text="Clear selected", command=remove_selected).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Close", command=window.destroy).pack(side=tk.RIGHT)

    def _show_history(self) -> None:
        batches = batch_history()
        window = tk.Toplevel(self.root)
        window.title(f"{GUI_TITLE} history & restoration")
        window.geometry("880x460")

        paned = ttk.PanedWindow(window, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Left pane: Batches
        left_frame = ttk.Labelframe(paned, text="Applied Batches", padding=6)
        paned.add(left_frame, weight=1)
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(0, weight=1)

        batch_cols = ("status", "date", "root")
        batch_tree = ttk.Treeview(left_frame, columns=batch_cols, show="headings", selectmode="browse")
        batch_tree.heading("status", text="Status")
        batch_tree.heading("date", text="Date")
        batch_tree.heading("root", text="Folder")
        batch_tree.column("status", width=90)
        batch_tree.column("date", width=130)
        batch_tree.column("root", width=180)

        batch_scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=batch_tree.yview)
        batch_tree.configure(yscrollcommand=batch_scrollbar.set)
        batch_tree.grid(row=0, column=0, sticky=tk.NSEW)
        batch_scrollbar.grid(row=0, column=1, sticky=tk.NS)

        # Right pane: Actions in selected batch
        right_frame = ttk.Labelframe(paned, text="Changed Files in Batch", padding=6)
        paned.add(right_frame, weight=2)
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=1)

        action_cols = ("file", "action", "status")
        action_tree = ttk.Treeview(right_frame, columns=action_cols, show="headings", selectmode="extended")
        action_tree.heading("file", text="File / Target")
        action_tree.heading("action", text="Kind")
        action_tree.heading("status", text="Status")
        action_tree.column("file", width=280)
        action_tree.column("action", width=70)
        action_tree.column("status", width=80)

        action_scrollbar = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=action_tree.yview)
        action_tree.configure(yscrollcommand=action_scrollbar.set)
        action_tree.grid(row=0, column=0, sticky=tk.NSEW)
        action_scrollbar.grid(row=0, column=1, sticky=tk.NS)

        batch_data_map = {}
        for b in batches:
            bid = b.get("batch_id", "")
            if not bid:
                continue
            batch_data_map[bid] = b
            st = b.get("status", "unknown")
            dt = _format_local_timestamp(b.get("created_at", ""))
            rt = b.get("root", "")
            batch_tree.insert("", tk.END, iid=bid, values=(st, dt, rt))

        action_group_map = {}

        def on_batch_select(event=None):
            action_tree.delete(*action_tree.get_children())
            action_group_map.clear()
            sel = batch_tree.selection()
            if not sel:
                return
            bid = sel[0]
            bdata = batch_data_map.get(bid)
            if not bdata:
                return
            actions = bdata.get("actions", [])
            for idx, act in enumerate(actions):
                f_path = act.get("path") or act.get("new") or act.get("old") or "File"
                f_name = Path(f_path).name
                kind = act.get("kind", "")
                st = act.get("status", "")
                group_id = act.get("decision_group_id") or path_key(f_path)
                iid = f"act_{idx}"
                action_group_map[iid] = group_id
                action_tree.insert("", tk.END, iid=iid, values=(f_name, kind, st))

        batch_tree.bind("<<TreeviewSelect>>", on_batch_select)

        bottom = ttk.Frame(window, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)

        def restore_selected_files():
            bsel = batch_tree.selection()
            asel = action_tree.selection()
            if not bsel or not asel:
                messagebox.showinfo("Selection required", "Select a batch and at least one file to restore.")
                return
            bid = bsel[0]
            group_ids = {action_group_map[iid] for iid in asel if iid in action_group_map}
            if not group_ids:
                return
            if not messagebox.askyesno(
                "Restore selected files",
                f"Restore {len(group_ids)} selected file(s) from batch history?",
            ):
                return
            results = undo_batch(bid, decision_group_ids=group_ids)
            succeeded = sum(1 for r in results if r.status == "succeeded")
            messagebox.showinfo("Restoration complete", f"Successfully restored {succeeded} file(s).")
            window.destroy()
            self.status_var.set(f"Restored {succeeded} file(s) from history.")

        def restore_entire_batch():
            bsel = batch_tree.selection()
            if not bsel:
                messagebox.showinfo("Selection required", "Select a batch to restore.")
                return
            bid = bsel[0]
            if not messagebox.askyesno(
                "Restore entire batch",
                "Restore all changes from this batch in history?",
            ):
                return
            results = undo_batch(bid)
            succeeded = sum(1 for r in results if r.status == "succeeded")
            messagebox.showinfo("Restoration complete", f"Successfully restored batch ({succeeded} actions).")
            window.destroy()
            self.status_var.set(f"Restored batch ({succeeded} actions).")

        ttk.Button(bottom, text="Restore selected file(s)", command=restore_selected_files).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bottom, text="Restore entire batch", command=restore_entire_batch).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Close", command=window.destroy).pack(side=tk.RIGHT)

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
            if self.plan.tag_proposals:
                self.notebook.select(self.tabs["tags"])
            elif self.plan.rename_proposals:
                self.notebook.select(self.tabs["renames"])
            if not actions and self.plan.issues:
                self.notebook.select(self.tabs["errors"])
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
        selected_rows = list(tree.selection()) if row in tree.selection() else [row]
        selected_proposals = []
        seen_ids = set()
        for r in selected_rows:
            item_id = row_ids.get((tree_name, r), "")
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                p = self._proposal_for_id(item_id)
                if p is not None:
                    selected_proposals.append(p)

        if selected_proposals:
            menu.add_separator()
            q_label = (
                "Ignore this match in future"
                if len(selected_proposals) == 1
                else f"Ignore {len(selected_proposals)} selected matches in future"
            )
            menu.add_command(
                label=q_label,
                command=lambda ps=selected_proposals: self._quarantine_proposals(ps),
            )
            menu.add_command(
                label="Quarantine manager…",
                command=self._show_quarantine_manager,
            )

            proposal = selected_proposals[0]
            if tree_name == "tags" and proposal.evidence:
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

    def _active_action_scope(self) -> tuple[str, tuple] | None:
        active_tab = str(self.notebook.select())
        if active_tab == str(self.tabs["renames"]):
            return "filename", tuple(self.plan.rename_proposals)
        if active_tab == str(self.tabs["tags"]):
            return "metadata", tuple(self.plan.tag_proposals)
        self.status_var.set(
            "Open Filename changes or Metadata changes before selecting changes."
        )
        return None

    def _select_recommended(self) -> None:
        if self.plan is None:
            return
        scope = self._active_action_scope()
        if scope is None:
            return
        scope_name, items = scope
        recommended = {
            item.id for item in items if _is_high_confidence_action(item)
        }
        self._set_selected_ids(recommended, expand_groups=False)
        self.status_var.set(
            f"Selected {len(recommended)} recommended {scope_name} change(s)."
        )

    def _select_all(self) -> None:
        if self.plan is None:
            return
        scope = self._active_action_scope()
        if scope is None:
            return
        scope_name, items = scope
        selected = {
            item.id
            for item in items
            if item.apply_eligible and not item.requires_review
        }
        self._set_selected_ids(selected, expand_groups=False)
        skipped = len(items) - len(selected)
        self.status_var.set(
            f"Selected {len(selected)} ready {scope_name} change(s); "
            f"{skipped} need review or are blocked."
        )

    def _select_artwork(self) -> None:
        if self.plan is None:
            return
        artwork = _artwork_ids(self.plan)
        self._set_selected_ids(artwork, expand_groups=False)
        selected_artwork = len(self.selected_ids & artwork)
        skipped = sum(
            item.artwork_after is not None and not item.apply_eligible
            for item in self.plan.tag_proposals
        )
        self.notebook.select(self.tabs["tags"])
        self.status_var.set(
            f"Selected {selected_artwork} verified cover-art change(s)"
            + (f"; {skipped} blocking item(s) skipped." if skipped else ".")
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

    def _set_selected_ids(self, selected_ids, *, expand_groups: bool = True) -> None:
        plan = getattr(self, "plan", None)
        selected = (
            _expand_group_selection(
                plan,
                selected_ids,
                include_review=True,
            )
            if plan is not None and expand_groups
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
