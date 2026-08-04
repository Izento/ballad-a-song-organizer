"""User-initiated analysis, apply, undo, and edit commands."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from gui.dialogs.filename_edit import _ask_filename
from gui.presentation import filename_validation_error
from renamer.apply import batches_requiring_recovery, latest_undoable_batch
from renamer.domain.issues import ReviewIssue
from renamer.proposal_selection import action_items, grouped_action_ids
from renamer.quarantine import quarantine_file
from renamer.review_models import canonical_path, path_key, proposal_id
from renamer.review_service import coordinate_tag_proposals, refresh_rename_readiness


class ActionControllerMixin:
    """Start safe background work and manage user-approved proposal changes."""

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="Choose music folder")
        if selected:
            self.folder_var.set(selected)
            self._reset_review_state()

    def _reset_review_state(self) -> None:
        self.session.plan = None
        self.session.selected_ids.clear()
        self.session.applied_group_ids.clear()
        self.session.recovery_overrides.clear()
        self.session.duplicate_selected_paths.clear()
        self._clear_trees()
        self._clear_activity_log()
        self._update_review_details(None)
        self.status_var.set("Folder selected. Click Organize library to analyze.")
        self._update_primary_button()
        self._update_duplicate_remove_button()

    def _organize_library(self) -> None:
        folder = self.folder_var.get().strip()
        if not self._analysis_is_ready(folder):
            return
        if not self._confirm_analysis():
            return
        removed_artwork = self._resolve_shared_folder_artwork(folder)
        if removed_artwork is None:
            return
        self._prepare_analysis(folder, removed_artwork)
        self._start_analysis(folder)

    def _analysis_is_ready(self, folder: str) -> bool:
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Folder required", "Choose an existing music folder.")
            return False
        if not self.musicbrainz_available:
            messagebox.showerror(
                "MusicBrainz unavailable",
                "Metadata enrichment cannot run because musicbrainzngs is not installed "
                "in this Python environment. Start Ballad with 'uv run ballad'.",
            )
            return False
        return True

    def _confirm_analysis(self) -> bool:
        return messagebox.askyesno(
            "Organize library",
            "Ballad will identify songs, enrich metadata, and prepare a reviewable "
            "plan of filename changes, tag changes, and cover art.\n\nNothing on "
            "disk changes until you select proposals below and click 'Apply selected'. "
            "Duplicate findings can be reviewed separately for Recycle Bin removal.\n\nContinue?",
        )

    def _prepare_analysis(self, folder: str, removed_artwork) -> None:
        self._reset_review_state()
        if removed_artwork:
            self._append_activity_log(
                "Removed shared folder artwork: " + ", ".join(path.name for path in removed_artwork)
            )
        self._append_activity_log(f"Starting analysis: {folder}")
        self._set_busy(True)
        self.status_var.set("Organizing library…")

    def _start_analysis(self, folder: str) -> None:
        acoustid_key = self.acoustid_key if self.online_identification_var.get() else None
        self.session.last_run_checked_duplicates = self.duplicate_check_var.get()
        self.jobs.organize(
            folder,
            recursive=self.recursive_var.get(),
            fingerprint=self.fingerprint_var.get(),
            acoustid_key=acoustid_key,
            include_artwork=self.cover_art_var.get(),
            include_duplicates=self.session.last_run_checked_duplicates,
            include_renames=self.propose_renames_var.get(),
        )

    def _apply(self) -> None:
        plan = self.session.plan
        if plan is None:
            messagebox.showinfo("Nothing to apply", "Organize a library first.")
            return
        if not self.session.selected_ids:
            messagebox.showinfo("Nothing selected", "Select at least one proposal.")
            return
        if not self._can_apply_plan(plan):
            return
        groups = self._selection_group_count()
        if not self._confirm_apply(groups):
            return
        self._append_activity_log(f"Applying selected changes for {groups} song(s).")
        self._set_busy(True)
        self.jobs.apply(plan, tuple(self.session.selected_ids))

    def _remove_selected_duplicates(self) -> None:
        targets = self._selected_duplicate_findings()
        if not targets:
            self.status_var.set("Select duplicate files to move to the Recycle Bin.")
            return
        plan = self.session.plan
        if plan is None or not plan.validate_digest():
            messagebox.showerror(
                "Duplicate findings expired",
                "The reviewed duplicate findings are stale. Organize the library again.",
            )
            return
        if not self._confirm_duplicate_removal(targets):
            return
        count = sum(len(paths) for _finding, paths in targets)
        self._append_activity_log(f"Moving {count} duplicate file(s) to the Recycle Bin.")
        self._set_busy(True)
        self.jobs.remove_duplicates(targets)

    def _selected_duplicate_findings(self) -> list[tuple[Any, tuple[str, ...]]]:
        plan = self.session.plan
        if plan is None:
            return []
        selected = self.session.duplicate_selected_paths
        return [
            (
                finding,
                tuple(path for path in finding.paths if path in selected.get(finding.id, set())),
            )
            for finding in plan.duplicate_findings
            if selected.get(finding.id)
        ]

    def _confirm_duplicate_removal(self, targets: list[tuple[Any, tuple[str, ...]]]) -> bool:
        details = []
        for finding, selected in targets:
            retained = [path for path in finding.paths if path not in selected]
            details.append(
                f"{finding.classification}:\n"
                f"  Move: {', '.join(Path(path).name for path in selected)}\n"
                f"  Keep: {', '.join(Path(path).name for path in retained)}"
            )
        return messagebox.askyesno(
            "Move duplicates to Recycle Bin",
            "The following files will be moved to the Windows Recycle Bin:\n\n"
            + "\n\n".join(details)
            + "\n\nYou can restore them from the Recycle Bin.",
        )

    def _can_apply_plan(self, plan) -> bool:
        pending = batches_requiring_recovery(plan.root)
        if pending and not self._confirm_recovery_override(pending, plan.root):
            return False
        if plan.validate_digest():
            return True
        messagebox.showerror(
            "Plan invalid", "The reviewed plan no longer matches its digest. Organize again."
        )
        return False

    def _confirm_apply(self, groups: int) -> bool:
        return messagebox.askyesno(
            "Confirm selected changes",
            f"Apply the coordinated changes for {groups} selected song(s)?\n\n"
            "The reviewed plan will be revalidated before any file is changed.",
        )

    def _cancel(self) -> None:
        if self.jobs.active:
            self.jobs.cancel()
            self._append_activity_log("Cancellation requested.")
            self.status_var.set("Cancellation requested…")

    def _handle_quarantine_button_click(self) -> None:
        proposals = self._selected_proposals_in_active_tree()
        if not proposals:
            self.status_var.set("Select one or more changes to quarantine.")
            return
        self._quarantine_proposals(proposals)

    def _selected_proposals_in_active_tree(self) -> list[Any]:
        scope = self._active_action_scope()
        if scope is None:
            return []
        tree_name = "changes"
        proposals = self._proposals_from_selected_rows(tree_name)
        if proposals:
            return proposals
        plan = self.session.plan
        return (
            [item for item in action_items(plan) if item.id in self.session.selected_ids]
            if plan
            else []
        )

    def _proposals_from_selected_rows(self, tree_name: str) -> list[Any]:
        tree = self.trees.get(tree_name)
        if tree is None:
            return []
        seen, proposals = set(), []
        for row in tree.selection():
            group_id = self.session.row_group_ids.get((tree_name, row))
            if group_id in seen:
                continue
            proposals.extend(self.session.proposals_for_group(group_id))
            seen.add(group_id)
        return proposals

    def _quarantine_proposals(self, proposals: list[Any]) -> None:
        targets = self._quarantine_targets(proposals)
        if not targets or not self._confirm_quarantine(targets):
            return
        group_ids, issues = self._save_quarantine_targets(targets)
        self._remove_quarantined_proposals(group_ids, issues)
        self.status_var.set(f"Added {len(targets)} file(s) to quarantine.")

    def _quarantine_targets(self, proposals: list[Any]) -> list[tuple[Any, str]]:
        targets, seen_paths = [], set()
        for proposal in proposals:
            path = getattr(proposal, "path", None) or getattr(proposal, "old_path", None)
            if path and path not in seen_paths:
                seen_paths.add(path)
                targets.append((proposal, path))
        return targets

    def _confirm_quarantine(self, targets: list[tuple[Any, str]]) -> bool:
        if len(targets) == 1:
            filename = Path(targets[0][1]).name
            prompt = (
                f"Ignore future online matches for '{filename}'?\n\nBallad will save "
                "this choice and skip online identification for this file on future "
                "runs. You can clear this anytime in Quarantine manager."
            )
        else:
            prompt = (
                f"Ignore future online matches for {len(targets)} selected files?\n\n"
                "Ballad will save these choices and skip online identification for "
                "these files on future runs. You can clear these anytime in "
                "Quarantine manager."
            )
        return messagebox.askyesno("Ignore matches in future", prompt)

    def _save_quarantine_targets(
        self, targets: list[tuple[Any, str]]
    ) -> tuple[set[str], list[ReviewIssue]]:
        groups, issues = set(), []
        for proposal, path in targets:
            values = proposal.after if hasattr(proposal, "after") else proposal.proposed_values
            quarantine_file(
                path,
                artist=str(values.get("artist") or ""),
                title=str(values.get("title") or ""),
                reason="Ignored by user during review",
            )
            groups.add(proposal.decision_group_id)
            issues.append(
                ReviewIssue.from_dict(
                    {
                        "category": "quarantined",
                        "path": path,
                        "message": "Match ignored by user quarantine.",
                    }
                )
            )
        return groups, issues

    def _remove_quarantined_proposals(
        self, group_ids: set[str], new_issues: list[ReviewIssue]
    ) -> None:
        plan = self.session.plan
        if plan is None:
            return
        renames = tuple(
            item for item in plan.rename_proposals if item.decision_group_id not in group_ids
        )
        tags = tuple(item for item in plan.tag_proposals if item.decision_group_id not in group_ids)
        removed_ids = {
            item_id
            for group_id, item_ids in grouped_action_ids(plan).items()
            if group_id in group_ids
            for item_id in item_ids
        }
        self.session.selected_ids.difference_update(removed_ids)
        updated = plan.with_proposals(renames, tags)
        updated = replace(updated, issues=tuple((*plan.issues, *new_issues)), digest="")
        self.session.plan = replace(updated, digest=updated._computed_digest())
        self._populate_plan(self.session.plan)
        self._set_selected_ids(self.session.selected_ids)

    def _undo_latest(self) -> None:
        root = (
            self.session.plan.root
            if self.session.plan is not None
            else self.folder_var.get().strip()
        )
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

    def _edit_selected_filename(self) -> None:
        proposal = self._selected_rename_proposal()
        if proposal is None or self._rename_edit_is_blocked(proposal):
            return
        filename = _ask_filename(self.root, proposal.proposed_values.get("filename", ""))
        if filename is None:
            return
        new_path = self._validated_rename_path(proposal, filename.strip())
        if new_path is None:
            return
        self._replace_rename_proposal(proposal, new_path, filename.strip())

    def _selected_rename_proposal(self):
        plan = self.session.plan
        if plan is None:
            messagebox.showinfo("Nothing to edit", "Organize a library first.")
            return None
        rows = self.trees["changes"].selection()
        if len(rows) != 1:
            messagebox.showinfo("Choose a rename", "Click one song row, then choose Edit filename.")
            return None
        group_id = self.session.row_group_ids.get(("changes", rows[0]))
        return next(
            (
                item
                for item in self.session.proposals_for_group(group_id)
                if hasattr(item, "old_path")
            ),
            None,
        )

    def _rename_edit_is_blocked(self, proposal) -> bool:
        if proposal is not None and not self._group_was_applied(proposal):
            return False
        messagebox.showinfo(
            "Rename already applied" if proposal else "Rename unavailable",
            "Organize the library again before editing this song."
            if proposal
            else "That rename is no longer available.",
        )
        return True

    def _validated_rename_path(self, proposal, filename: str) -> str | None:
        error = filename_validation_error(filename, proposal.old_path)
        if error:
            messagebox.showerror("Invalid filename", error)
            return None
        new_path = canonical_path(str(Path(proposal.old_path).with_name(filename)))
        if path_key(new_path) == path_key(proposal.old_path):
            messagebox.showerror(
                "No change", "The corrected filename must differ from the current filename."
            )
            return None
        if self._rename_destination_is_taken(proposal, new_path):
            messagebox.showerror(
                "Filename already proposed", "Another reviewed rename already uses that filename."
            )
            return None
        return new_path

    def _rename_destination_is_taken(self, proposal, new_path: str) -> bool:
        return any(
            item.id != proposal.id and path_key(item.new_path) == path_key(new_path)
            for item in self.session.plan.rename_proposals
        )

    def _replace_rename_proposal(self, proposal, new_path: str, filename: str) -> None:
        updated = replace(
            proposal,
            id=proposal_id("rename", proposal.old_path, new_path),
            new_path=new_path,
            proposed_values={**proposal.proposed_values, "filename": filename},
            reason=f"{proposal.reason} Filename corrected during review.",
        )
        plan = self.session.plan
        renames = refresh_rename_readiness(
            tuple(updated if item.id == proposal.id else item for item in plan.rename_proposals)
        )
        tags, _, _ = coordinate_tag_proposals(renames, list(plan.tag_proposals))
        was_selected = proposal.id in self.session.selected_ids
        self.session.plan = plan.with_proposals(renames, tags)
        self._populate_plan(self.session.plan)
        selected = grouped_action_ids(self.session.plan).get(updated.decision_group_id, set())
        self._set_selected_ids(
            selected if was_selected else self.session.selected_ids - {proposal.id}
        )
        self.status_var.set(f"Corrected proposed filename to {filename}.")


__all__ = ["ActionControllerMixin"]
