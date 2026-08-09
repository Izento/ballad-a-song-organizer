"""Background job event polling and presentation."""

from __future__ import annotations

import queue
from pathlib import Path
from tkinter import messagebox

from gui.presentation import format_progress_log
from gui.protocols import GuiAppProtocol
from renamer.proposal_selection import (
    action_items,
    grouped_action_ids,
    recommended_ids,
    requires_review,
)


class EventControllerMixin(GuiAppProtocol):
    """Dispatch worker events without coupling workers to Tk widgets."""

    def _poll_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        handlers = {
            "progress": self._handle_progress_event,
            "organize-complete": self._handle_organize_complete,
            "undo-complete": self._handle_undo_complete,
            "apply-complete": self._handle_apply_complete,
            "duplicate-remove-complete": self._handle_duplicate_remove_complete,
            "failed": self._handle_failed_event,
        }
        handler = handlers.get(event[0])
        if handler is not None:
            handler(event)

    def _handle_progress_event(self, event: tuple) -> None:
        _, stage, current, total, path = event
        message = format_progress_log(stage, current, total, path)
        self._append_activity_log(message)
        self.status_var.set(message)

    def _handle_organize_complete(self, event: tuple) -> None:
        self.session.plan, results = event[1], event[2]
        self._set_busy(False)
        self._populate_plan(self.session.plan)
        self._record_applied_groups(results)
        self._select_review_tab()
        self._show_analysis_summary()

    def _select_review_tab(self) -> None:
        plan = self.session.plan
        if plan.rename_proposals or plan.tag_proposals:
            self.notebook.select(self.tabs["changes"])
        elif plan.duplicate_findings:
            self.notebook.select(self.tabs["duplicates"])

    def _show_analysis_summary(self) -> None:
        plan = self.session.plan
        actions = action_items(plan)
        if not actions and plan.issues:
            self._show_no_actions_summary(plan)
            return
        unresolved = len(plan.issues) + sum(requires_review(item) for item in actions)
        summary = self._analysis_summary(plan, actions, unresolved)
        self.status_var.set(summary)
        self._append_activity_log(summary)

    def _show_no_actions_summary(self, plan) -> None:
        self.notebook.select(self.tabs["errors"])
        summary = f"No proposed changes found; {len(plan.issues)} file(s) failed analysis."
        self.status_var.set(summary)
        self._append_activity_log(f"Analysis complete: {summary.lower()}")
        messagebox.showwarning(
            "No songs were organized",
            "Ballad could not produce a metadata or filename change to review. "
            "Open Skipped / errors for the causes.",
        )

    def _analysis_summary(self, plan, actions, unresolved: int) -> str:
        duplicate_summary = (
            f"{len(plan.duplicate_findings)} duplicate findings."
            if self.session.last_run_checked_duplicates
            else "duplicate check skipped."
        )
        return (
            f"Analysis complete: {len(actions)} proposed change(s) across "
            f"{len(grouped_action_ids(plan))} song(s), {len(recommended_ids(plan))} "
            f"high-confidence and recommended, {unresolved} item(s) need review, "
            f"{duplicate_summary} Nothing has changed yet — select changes and click "
            "'Apply selected'. Duplicate findings can be reviewed separately."
        )

    def _handle_undo_complete(self, event: tuple) -> None:
        results = event[1]
        self._set_busy(False)
        succeeded = sum(result.status == "succeeded" for result in results)
        failures = [result for result in results if result.status == "failed"]
        summary = f"Undo complete: {succeeded} restored, {len(failures)} failed."
        self.status_var.set(summary)
        self._append_activity_log(summary)
        if failures:
            self._show_undo_failures(failures)

    def _show_undo_failures(self, failures) -> None:
        details = "\n".join(
            f"• {Path(result.path).name}: {result.message}" for result in failures[:10]
        )
        if len(failures) > 10:
            details += f"\n… and {len(failures) - 10} more."
        messagebox.showwarning(
            "Undo needs attention",
            f"{len(failures)} action(s) could not be restored:\n\n{details}\n\n"
            "This is often a transient file lock (e.g. antivirus scanning); "
            "click 'Undo latest' again to retry.",
        )

    def _handle_apply_complete(self, event: tuple) -> None:
        results = event[1]
        self._record_applied_groups(results)
        self._set_busy(False)
        summary, blocked, failed = self._apply_summary(results)
        self._insert_apply_errors(results)
        self.status_var.set(summary)
        self._append_activity_log(summary)
        if failed or blocked:
            self._show_apply_issues(summary, blocked, failed)

    def _handle_duplicate_remove_complete(self, event: tuple) -> None:
        results = event[1]
        succeeded = [result for result in results if result.status == "succeeded"]
        failures = [result for result in results if result.status != "succeeded"]
        self._set_busy(False)
        if succeeded:
            self._reset_review_state()
        summary = (
            f"Duplicate removal complete: {len(succeeded)} moved to Recycle Bin, "
            f"{len(failures)} failed."
        )
        self.status_var.set(summary)
        self._append_activity_log(summary)
        if succeeded:
            self.status_var.set(summary + " Organize the folder again to refresh the review.")
        if failures:
            self._show_duplicate_remove_failures(failures)

    def _show_duplicate_remove_failures(self, failures) -> None:
        details = "\n".join(
            f"• {Path(result.path).name}: {result.message}" for result in failures[:10]
        )
        if len(failures) > 10:
            details += f"\n… and {len(failures) - 10} more."
        messagebox.showwarning(
            "Duplicate removal needs attention",
            f"{len(failures)} file(s) could not be moved:\n\n{details}",
        )

    def _apply_summary(self, results) -> tuple[str, int, int]:
        succeeded = sum(result.status == "succeeded" for result in results)
        blocked = sum(result.status == "blocked" for result in results)
        failed = sum(result.status in {"failed", "stale"} for result in results)
        return (
            f"Apply complete: {succeeded} succeeded, {blocked} blocked, {failed} failed.",
            blocked,
            failed,
        )

    def _insert_apply_errors(self, results) -> None:
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

    def _show_apply_issues(self, summary: str, blocked: int, failed: int) -> None:
        detail = (
            "Blocked actions were skipped; the successful actions do not need to be undone. "
            if blocked and not failed
            else "Use Undo latest to restore successful actions when a mutation failed. "
        )
        messagebox.showwarning(
            "Apply finished with issues", f"{summary} {detail}Open the error tab for details."
        )

    def _handle_failed_event(self, event: tuple) -> None:
        self._set_busy(False)
        self.status_var.set("Operation failed.")
        self._append_activity_log(f"Operation failed: {event[1]}")
        messagebox.showerror("Operation failed", event[1])


__all__ = ["EventControllerMixin"]
