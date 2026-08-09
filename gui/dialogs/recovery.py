"""Recovery override confirmation for incomplete undo batches."""

from __future__ import annotations

from tkinter import messagebox

from gui.protocols import GuiAppProtocol
from renamer.review_models import path_key


class RecoveryDialogMixin(GuiAppProtocol):
    """Remember explicitly confirmed recovery overrides per library root."""

    def _confirm_recovery_override(self, pending: list[dict], root: str) -> bool:
        root_key = path_key(root)
        if root_key in self.session.recovery_overrides:
            return True
        batch_word = "batch" if len(pending) == 1 else "batches"
        if not messagebox.askyesno(
            "Recovery still unresolved",
            self._recovery_message(len(pending), batch_word),
        ):
            return False
        self.session.recovery_overrides.add(root_key)
        self._append_activity_log("Continuing despite unresolved recovery for this folder.")
        return True

    def _recovery_message(self, count: int, batch_word: str) -> str:
        return (
            f"{count} incomplete recovery {batch_word} for this folder still contain "
            "actions that could not be restored.\n\nYou can retry 'Undo latest', "
            "or continue with the selected changes anyway. Continuing will not "
            "repair the missing file and may require manual cleanup of the older "
            "batch.\n\nContinue applying the selected changes?"
        )


__all__ = ["RecoveryDialogMixin"]
