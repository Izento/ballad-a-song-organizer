"""Review-first tkinter application composition shell."""

from __future__ import annotations

import os
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from tkinter import messagebox, ttk

from gui.controllers.actions import ActionControllerMixin
from gui.controllers.context_menu import ContextMenuMixin
from gui.controllers.events import EventControllerMixin
from gui.controllers.selection import SelectionControllerMixin
from gui.dialogs.history import HistoryDialogMixin
from gui.dialogs.quarantine import QuarantineDialogMixin
from gui.dialogs.recovery import RecoveryDialogMixin
from gui.dialogs.shared_artwork import SharedArtworkDialogMixin
from gui.presentation import format_local_timestamp as _format_local_timestamp  # noqa: F401
from gui.presentation import format_progress_log as _format_progress_log  # noqa: F401
from gui.presentation import plan_rows  # noqa: F401
from gui.presentation import shared_folder_artwork as _shared_folder_artwork  # noqa: F401
from gui.presentation import tag_display as _tag_display  # noqa: F401
from gui.session import ReviewSession
from gui.theme import (
    _PRIMARY_BUTTON_ACTIVE_BG,
    _PRIMARY_BUTTON_BG,
    _PRIMARY_BUTTON_DISABLED_FG,
    _SHIFT_MASK,  # noqa: F401
)
from gui.views.activity_sidebar import ActivitySidebarMixin
from gui.views.review_details import ReviewDetailsMixin
from gui.widgets.tooltip import _add_tooltip
from gui.widgets.tree import TreeMixin
from gui.workers import BackgroundJobs
from renamer.musicbrainz import is_available as musicbrainz_available
from renamer.proposal_selection import requires_review as _requires_review  # noqa: F401
from renamer.runtime import (
    ensure_app_dirs,
    resolve_acoustid_key,
    resolve_fpcalc,
    resource_path,
)

GUI_TITLE = "Ballad"
_WINDOWS_APP_ID = "Ballad.SongOrganizer"


def _set_windows_app_identity() -> None:
    """Use a stable Windows application ID when the platform supports it."""
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


class SongOrganizerApp(
    ActionControllerMixin,
    EventControllerMixin,
    SelectionControllerMixin,
    ContextMenuMixin,
    QuarantineDialogMixin,
    HistoryDialogMixin,
    SharedArtworkDialogMixin,
    RecoveryDialogMixin,
    ReviewDetailsMixin,
    ActivitySidebarMixin,
    TreeMixin,
):
    """Compose Tk widgets, focused controllers, and one review session."""

    def __init__(self, root: tk.Tk | None = None):
        _set_windows_app_identity()
        self.root = root or tk.Tk()
        self._icon_handles: tuple[object, ...] = ()
        self.root.title(GUI_TITLE)
        self._set_window_icon()
        self.root.geometry("1180x720")
        self.root.minsize(900, 560)
        self._initialize_session()
        self._initialize_capabilities()
        self._initialize_controls()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_events)

    def _initialize_session(self) -> None:
        self.jobs = BackgroundJobs()
        self.events = self.jobs.events
        self.session = ReviewSession()
        self._tree_headings: dict[str, dict[str, str]] = {}
        self._activity_sidebar_open = True

    def _initialize_capabilities(self) -> None:
        fpcalc_path = resolve_fpcalc()
        self.fpcalc_available = fpcalc_path is not None
        self.acoustid_key = resolve_acoustid_key()
        self.musicbrainz_available = musicbrainz_available()
        self._set_capability_text(fpcalc_path)

    def _set_capability_text(self, fpcalc_path: Path | None) -> None:
        fpcalc_state = "available" if fpcalc_path else "not installed (optional)"
        online_state = (
            "ready"
            if self.acoustid_key and fpcalc_path and self.musicbrainz_available
            else "MusicBrainz client missing"
            if not self.musicbrainz_available
            else "embedded IDs only"
        )
        self.capability_var = tk.StringVar(
            value=f"Fingerprint helper: {fpcalc_state} | Online identification: {online_state}"
        )

    def _initialize_controls(self) -> None:
        self.folder_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.duplicate_check_var = tk.BooleanVar(value=True)
        self.duplicate_check_var.trace_add("write", self._sync_fingerprint_availability)
        self.fingerprint_var = tk.BooleanVar(value=self.fpcalc_available)
        self.status_var = tk.StringVar(value="Choose a folder to begin.")
        self.online_identification_var = tk.BooleanVar(
            value=bool(self.acoustid_key and self.fpcalc_available)
        )
        self.cover_art_var = tk.BooleanVar(value=True)
        self.propose_renames_var = tk.BooleanVar(value=False)

    def _set_window_icon(self) -> None:
        icon_path = resource_path("ballad.ico")
        if not icon_path.is_file():
            return
        self._set_tk_icon(icon_path)
        if os.name == "nt":
            self._set_windows_icon_handles(icon_path)

    def _set_tk_icon(self, icon_path: Path) -> None:
        for call in (
            lambda: self.root.iconbitmap(str(icon_path)),
            lambda: self.root.iconbitmap(default=str(icon_path)),
        ):
            with suppress(tk.TclError):
                call()

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
            self._icon_handles = self._load_icon_handles(load_image, send_message, icon_path)
        except (AttributeError, OSError, TypeError):
            self._icon_handles = ()

    def _load_icon_handles(self, load_image, send_message, icon_path: Path) -> tuple[object, ...]:
        hwnd, handles = self.root.winfo_id(), []
        for icon_size, icon_kind in ((32, 1), (16, 0)):
            handle = load_image(None, str(icon_path), 1, icon_size, icon_size, 0x10)
            if handle:
                send_message(hwnd, 0x0080, icon_kind, handle)
                handles.append(handle)
        return tuple(handles)

    def _build_ui(self) -> None:
        self._build_library_bar()
        self._build_options_bar()
        self._build_review_content()
        self._build_bottom_bar()

    def _build_library_bar(self) -> None:
        library = ttk.Labelframe(self.root, text="Library", padding=10)
        library.pack(fill=tk.X, padx=10, pady=(10, 6))
        row = ttk.Frame(library)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Music folder:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.folder_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 5)
        )
        ttk.Button(row, text="Browse…", command=self._browse).pack(side=tk.LEFT)
        ttk.Checkbutton(row, text="Include subfolders", variable=self.recursive_var).pack(
            side=tk.LEFT, padx=(10, 0)
        )
        ttk.Label(library, textvariable=self.capability_var).pack(anchor=tk.W, pady=(6, 0))

    def _build_options_bar(self) -> None:
        options = ttk.Labelframe(self.root, text="Identification & duplicate detection", padding=10)
        options.pack(fill=tk.X, padx=10, pady=(0, 6))
        self._build_online_option(options)
        self._build_cover_art_option(options)
        self._build_rename_option(options)
        self._build_duplicate_option(options)
        self._build_fingerprint_option(options)
        self._sync_fingerprint_availability()

    def _build_online_option(self, parent) -> None:
        self.online_identification_check = ttk.Checkbutton(
            parent,
            text="Use AcoustID identification",
            variable=self.online_identification_var,
            state=tk.NORMAL if self.acoustid_key and self.fpcalc_available else tk.DISABLED,
        )
        self.online_identification_check.pack(side=tk.LEFT)
        _add_tooltip(
            self.online_identification_check,
            "Identify songs with missing or unreliable tags by audio fingerprint via "
            "AcoustID, then look up the match in MusicBrainz.",
        )

    def _build_cover_art_option(self, parent) -> None:
        self.cover_art_check = ttk.Checkbutton(
            parent, text="Embed missing front cover art", variable=self.cover_art_var
        )
        self.cover_art_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.cover_art_check,
            "Download and embed front cover art from the Cover Art Archive for files "
            "that don't already have any.",
        )

    def _build_rename_option(self, parent) -> None:
        self.propose_renames_check = ttk.Checkbutton(
            parent, text="Propose filename changes", variable=self.propose_renames_var
        )
        self.propose_renames_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.propose_renames_check,
            "Suggest renaming files on disk based on enriched metadata. When unchecked, "
            "Ballad enriches tags and embeds cover art without changing filenames.",
        )

    def _build_duplicate_option(self, parent) -> None:
        self.duplicate_check_check = ttk.Checkbutton(
            parent, text="Check for duplicate files", variable=self.duplicate_check_var
        )
        self.duplicate_check_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.duplicate_check_check,
            "Hashes every file to find exact and same-song duplicates. Turning this "
            "off skips hashing and can speed up large libraries.",
        )

    def _build_fingerprint_option(self, parent) -> None:
        self.fingerprint_check = ttk.Checkbutton(
            parent,
            text="Fingerprint audio for stronger duplicate matches",
            variable=self.fingerprint_var,
        )
        self.fingerprint_check.pack(side=tk.LEFT, padx=(16, 0))
        _add_tooltip(
            self.fingerprint_check,
            "Also computes acoustic fingerprints to identify re-encoded duplicate files. "
            "It has no effect while duplicate checking is off.",
        )

    def _build_review_content(self) -> None:
        content = ttk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(content)
        self.notebook.grid(row=0, column=0, sticky=tk.NSEW)
        self.trees, self.tabs = {}, {}
        for key, title in (
            ("renames", "Filename changes"),
            ("tags", "Metadata changes"),
            ("duplicates", "Duplicate findings (read-only)"),
            ("errors", "Skipped / errors"),
        ):
            frame = ttk.Frame(self.notebook, padding=6)
            self.notebook.add(frame, text=title)
            self.tabs[key] = frame
            self.trees[key] = self._make_tree(frame, key)
        self._build_activity_sidebar(content)

    def _build_bottom_bar(self) -> None:
        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        bottom.columnconfigure(1, weight=1)
        self._build_selection_controls(bottom)
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=1, sticky=tk.EW, padx=12)
        self._build_action_controls(bottom)

    def _build_selection_controls(self, parent) -> None:
        controls = ttk.Frame(parent)
        controls.grid(row=0, column=0, sticky=tk.W)
        for text, command in (
            ("Select recommended", self._select_recommended),
            ("Select all ready", self._select_all),
            ("Select missing artwork", self._select_artwork),
        ):
            ttk.Button(controls, text=text, command=command).pack(side=tk.LEFT, padx=(8, 0))
        self.edit_button = ttk.Button(
            controls, text="Edit filename", command=self._edit_selected_filename
        )
        self.edit_button.pack(side=tk.LEFT, padx=(8, 0))

    def _build_action_controls(self, parent) -> None:
        actions = ttk.Frame(parent)
        actions.grid(row=0, column=2, sticky=tk.E)
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self._cancel)
        self.history_button = ttk.Button(actions, text="History", command=self._show_history)
        self.quarantine_button = ttk.Button(
            actions, text="Quarantine", command=self._handle_quarantine_button_click
        )
        self.undo_button = ttk.Button(actions, text="Undo latest", command=self._undo_latest)
        for button in (
            self.cancel_button,
            self.history_button,
            self.quarantine_button,
            self.undo_button,
        ):
            button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Separator(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        self._build_primary_button(actions)

    def _build_primary_button(self, parent) -> None:
        self.primary_button = tk.Button(
            parent,
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

    def _sync_fingerprint_availability(self, *_args, busy: bool | None = None) -> None:
        busy = self.jobs.active if busy is None else busy
        self.fingerprint_check.configure(
            state=tk.NORMAL if self.duplicate_check_var.get() and not busy else tk.DISABLED
        )

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.online_identification_check.configure(
            state=tk.DISABLED
            if busy or not (self.acoustid_key and self.fpcalc_available)
            else tk.NORMAL
        )
        for widget in (
            self.cover_art_check,
            self.propose_renames_check,
            self.duplicate_check_check,
            self.edit_button,
            self.history_button,
            self.quarantine_button,
            self.undo_button,
        ):
            widget.configure(state=state)
        self._sync_fingerprint_availability(busy=busy)
        self.cancel_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        if busy:
            self.primary_button.configure(state=tk.DISABLED)
            self.status_var.set("Working…")
        else:
            self._update_primary_button()

    def _update_primary_button(self) -> None:
        if not hasattr(self, "primary_button"):
            return
        if self.session.plan is None:
            self.primary_button.configure(
                text="Organize library", command=self._organize_library, state=tk.NORMAL
            )
            return
        count = self._selection_group_count()
        self.primary_button.configure(
            text=f"Apply selected ({count})" if count else "Apply selected",
            command=self._apply,
            state=tk.NORMAL if count else tk.DISABLED,
        )

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
    """Initialize application storage and run the Tk event loop."""
    ensure_app_dirs()
    _set_windows_app_identity()
    root = tk.Tk()
    SongOrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
