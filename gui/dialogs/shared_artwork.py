"""Confirmation flow for player-wide folder artwork."""

from __future__ import annotations

from pathlib import Path
from tkinter import messagebox

from gui.presentation import shared_folder_artwork
from gui.protocols import GuiAppProtocol
from gui.theme import _SHARED_ARTWORK_PREVIEW_LIMIT


class SharedArtworkDialogMixin(GuiAppProtocol):
    """Ask before removing artwork that media players share across tracks."""

    def _resolve_shared_folder_artwork(self, folder: str) -> tuple[Path, ...] | None:
        artwork = shared_folder_artwork(folder)
        if not artwork:
            return ()
        remove = messagebox.askyesnocancel(
            "Shared folder artwork detected", self._shared_artwork_prompt(artwork)
        )
        if remove is None:
            return None
        if not remove:
            return ()
        return self._remove_shared_artwork(artwork)

    def _shared_artwork_prompt(self, artwork: tuple[Path, ...]) -> str:
        preview = artwork[:_SHARED_ARTWORK_PREVIEW_LIMIT]
        names = "\n".join(f"• {path.name}" for path in preview)
        remaining = len(artwork) - len(preview)
        if remaining:
            names += f"\n• …and {remaining} more"
        return (
            f"Ballad found {len(artwork)} artwork file(s) that media players can "
            "display for every song in this folder when a track has no embedded "
            f"cover:\n\n{names}\n\nRemove these shared images before analysis?\n\n"
            "Yes: permanently delete them and continue\n"
            "No: keep them and continue\n"
            "Cancel: stop"
        )

    def _remove_shared_artwork(self, artwork: tuple[Path, ...]) -> tuple[Path, ...] | None:
        removed = []
        for path in artwork:
            try:
                path.unlink()
            except OSError as exc:
                messagebox.showerror(
                    "Could not remove shared artwork",
                    f"Ballad could not remove {path.name}:\n\n{exc}",
                )
                return None
            removed.append(path)
        return tuple(removed)


__all__ = ["SharedArtworkDialogMixin"]
