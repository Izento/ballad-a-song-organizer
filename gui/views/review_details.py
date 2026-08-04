"""Selection-driven proposal inspection view."""

from __future__ import annotations

import hashlib
import io
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from PIL import Image, ImageTk

from gui.presentation import confidence_color, metadata_differences, proposal_evidence
from renamer.media import read_front_artwork


def _display_value(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "(none)"
    return str(value or "(none)")


def _short_identifier(value: object) -> str:
    text = str(value)
    return text if len(text) <= 16 else f"{text[:12]}…"


class ReviewDetailsMixin:
    """Show one proposal's metadata, evidence, and cover art."""

    def _on_tree_select(self, tree_name: str, _event=None) -> None:
        tree = self.trees.get(tree_name)
        if not tree or not tree.selection():
            return
        row = tree.selection()[0]
        if tree_name == "changes":
            group_id = self.session.row_group_ids.get((tree_name, row))
            if group_id:
                self._update_group_review_details(group_id)
            return
        if tree_name == "duplicates":
            self._update_duplicate_review_details(row)
            return
        item_id = self.session.row_ids.get((tree_name, row))
        proposal = self.session.proposal_for_id(item_id) if item_id else None
        if proposal is not None:
            self._update_review_details(proposal)

    def _load_tk_image_bytes(self, data: bytes, max_size=(110, 110)):
        try:
            image = Image.open(io.BytesIO(data))
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None

    def _load_tk_image_file(self, file_path: str, max_size=(110, 110)):
        try:
            image = Image.open(file_path)
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None

    def _update_review_details(self, proposal: Any) -> None:
        self._clear_review_details()
        if proposal is None:
            self._show_review_details_prompt()
            return
        container = ttk.Frame(self.details_content, padding=4)
        container.pack(fill=tk.BOTH, expand=True)
        self._render_component_details(container, proposal)

    def _update_group_review_details(self, group_id: str) -> None:
        proposals = self.session.proposals_for_group(group_id)
        self._clear_review_details()
        if not proposals:
            self._show_review_details_prompt()
            return
        container = ttk.Frame(self.details_content, padding=4)
        container.pack(fill=tk.BOTH, expand=True)
        self._render_group_summary(container, proposals)
        self._render_group_selection(container, proposals)
        for proposal in proposals:
            self._render_component_details(container, proposal)

    def _render_group_summary(self, container, proposals) -> None:
        path = getattr(proposals[0], "path", None) or getattr(proposals[0], "old_path", "")
        box = ttk.Labelframe(container, text="Reviewing", padding=6)
        box.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(box, text="Selected file", font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W)
        ttk.Label(box, text=Path(path).name or "Unknown file", wraplength=310).pack(anchor=tk.W)

    def _render_group_selection(self, container, proposals) -> None:
        box = ttk.Labelframe(container, text="Apply these changes", padding=6)
        box.pack(fill=tk.X, pady=(0, 8))
        self._component_vars = []
        for proposal in proposals:
            label = "Apply filename rename" if hasattr(proposal, "old_path") else "Apply metadata"
            if getattr(proposal, "artwork_after", None) is not None:
                label = "Apply metadata and cover art"
            variable = tk.BooleanVar(value=proposal.id in self.session.selected_ids)
            self._component_vars.append(variable)
            state = tk.DISABLED if not proposal.apply_eligible else tk.NORMAL
            ttk.Checkbutton(
                box,
                text=label,
                variable=variable,
                state=state,
                command=lambda item_id=proposal.id, value=variable: (
                    self._toggle_component_selection(item_id, value.get())
                ),
            ).pack(anchor=tk.W)
            if not proposal.apply_eligible:
                for issue in proposal.review_issues:
                    self._add_issue_label(box, issue)

    def _render_component_details(self, container, proposal) -> None:
        is_rename = hasattr(proposal, "old_path") and hasattr(proposal, "new_path")
        label = "Filename change" if is_rename else "Metadata updates"
        box = ttk.Labelframe(container, text=label, padding=6)
        box.pack(fill=tk.X, pady=(0, 8))
        file_path = self._render_details_summary(box, proposal)
        self._render_details_warnings(box, proposal)
        if is_rename:
            self._add_filename_row(box, proposal)
        else:
            self._render_metadata_proposal(box, proposal)
        self._render_provider_evidence(box, proposal)
        self._render_artwork_inspection(box, proposal, file_path)

    def _update_duplicate_review_details(self, row: str) -> None:
        target = self.session.duplicate_row_ids.get(("duplicates", row))
        plan = self.session.plan
        finding = (
            next((item for item in plan.duplicate_findings if item.id == target[0]), None)
            if plan is not None and target
            else None
        )
        self._clear_review_details()
        if finding is None:
            self._show_review_details_prompt()
            return
        box = ttk.Labelframe(self.details_content, text="Duplicate finding", padding=6)
        box.pack(fill=tk.X, padx=4, pady=4)
        ttk.Label(box, text=finding.recommendation, wraplength=320).pack(anchor=tk.W)
        ttk.Label(
            box,
            text=f"Confidence: {finding.confidence}\nFiles in group: {len(finding.paths)}",
        ).pack(anchor=tk.W, pady=(6, 0))

    def _clear_review_details(self) -> None:
        for child in self.details_content.winfo_children():
            child.destroy()
        self._preview_images.clear()

    def _show_review_details_prompt(self) -> None:
        ttk.Label(
            self.details_content,
            text=(
                "Select a song proposal in the table to inspect local tags, "
                "MusicBrainz evidence, warnings, and artwork."
            ),
            wraplength=320,
            justify=tk.LEFT,
            padding=10,
        ).pack(fill=tk.X)

    def _render_details_summary(self, container, proposal) -> str:
        file_path = getattr(proposal, "path", None) or getattr(proposal, "old_path", None) or ""
        ttk.Label(
            container,
            text="Source file",
            font=("TkDefaultFont", 9, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            container,
            text=Path(file_path).name or "Unknown file",
            wraplength=320,
        ).pack(anchor=tk.W, pady=(0, 2))
        confidence = str(getattr(proposal, "confidence", "medium")).upper()
        frame = ttk.Frame(container)
        frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(
            frame,
            text=f"Confidence: {confidence}",
            foreground=confidence_color(confidence),
            font=("TkDefaultFont", 9, "bold"),
        ).pack(side=tk.LEFT)
        return file_path

    def _render_details_warnings(self, container, proposal) -> None:
        issues = getattr(proposal, "review_issues", ()) or ()
        warnings = getattr(proposal, "warnings", ()) or ()
        if not (issues or warnings):
            return
        box = ttk.Labelframe(container, text="Warnings & Issues", padding=6)
        box.pack(fill=tk.X, pady=(0, 8))
        for issue in issues:
            self._add_issue_label(box, issue)
        issue_messages = tuple(getattr(issue, "message", "") for issue in issues)
        for warning in warnings:
            if not any(warning in message for message in issue_messages):
                self._add_warning_label(box, warning)

    def _add_issue_label(self, parent, issue) -> None:
        ttk.Label(
            parent,
            text=f"• {getattr(issue, 'message', str(issue))}",
            foreground="red" if not getattr(issue, "apply_eligible", True) else "#d9534f",
            wraplength=300,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=1)

    def _add_warning_label(self, parent, warning) -> None:
        ttk.Label(
            parent,
            text=f"• {warning}",
            foreground="#d9534f",
            wraplength=300,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=1)

    def _render_metadata_proposal(self, container, proposal) -> None:
        ttk.Label(container, text="Set metadata", font=("TkDefaultFont", 9, "bold")).pack(
            anchor=tk.W, pady=(0, 2)
        )
        changed = False
        for label, before, after in metadata_differences(proposal):
            if before != after:
                self._add_metadata_row(container, label, before, after)
                changed = True
        if getattr(proposal, "artwork_after", None) is not None:
            ttk.Label(container, text="Cover art: Add or replace embedded cover").pack(anchor=tk.W)
            changed = True
        if not changed:
            ttk.Label(container, text="No visible tag fields change.", foreground="gray").pack(
                anchor=tk.W
            )

    def _add_metadata_row(self, parent, label: str, before: object, after: object) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(row, text=label, font=("TkDefaultFont", 9, "bold")).pack(anchor=tk.W)
        ttk.Label(
            row,
            text=f"Current: {_display_value(before)}",
            foreground="gray",
            wraplength=300,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        ttk.Label(
            row,
            text=f"Set to: {_display_value(after)}",
            font=("TkDefaultFont", 9, "bold"),
            wraplength=300,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

    def _add_filename_row(self, parent, proposal) -> None:
        old_name, new_name = Path(proposal.old_path).name, Path(proposal.new_path).name
        ttk.Label(parent, text="Rename to", font=("TkDefaultFont", 9, "bold")).pack(
            anchor=tk.W, pady=(0, 2)
        )
        ttk.Label(
            parent,
            text=f"Current: {old_name}",
            foreground="gray",
            wraplength=300,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)
        ttk.Label(
            parent,
            text=f"New filename: {new_name}",
            font=("TkDefaultFont", 9, "bold"),
            wraplength=300,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

    def _render_provider_evidence(self, container, proposal) -> None:
        identification, musicbrainz = proposal_evidence(proposal)
        box = ttk.Labelframe(container, text="Verification", padding=6)
        box.pack(fill=tk.X, pady=(0, 8))
        if not (identification or musicbrainz):
            ttk.Label(box, text="No online provider evidence attached.", foreground="gray").pack(
                anchor=tk.W
            )
            return
        self._render_identification_evidence(box, identification)
        self._render_musicbrainz_evidence(box, musicbrainz)

    def _render_identification_evidence(self, parent, evidence: dict) -> None:
        score = evidence.get("score")
        if not score:
            return
        value = int(score * 100) if isinstance(score, float) and score <= 1.0 else score
        ttk.Label(
            parent,
            text=f"AcoustID Match: {value}%",
            font=("TkDefaultFont", 9, "bold"),
        ).pack(anchor=tk.W)

    def _render_musicbrainz_evidence(self, parent, evidence: dict) -> None:
        for label, key in (
            ("MusicBrainz recording", "recording_id"),
            ("MusicBrainz release", "release_id"),
        ):
            if evidence.get(key):
                ttk.Label(
                    parent,
                    text=f"{label}: {_short_identifier(evidence[key])}",
                    font=("TkDefaultFont", 8),
                ).pack(anchor=tk.W, pady=(2, 0))
        for label, key in (("Release", "release"), ("Release Date", "date")):
            if evidence.get(key):
                ttk.Label(parent, text=f"{label}: {evidence[key]}", wraplength=300).pack(
                    anchor=tk.W, pady=(2, 0)
                )

    def _render_artwork_inspection(self, container, proposal, file_path: str) -> None:
        box = ttk.Labelframe(container, text="Cover Art Inspection", padding=6)
        box.pack(fill=tk.X, pady=(0, 8))
        row = ttk.Frame(box)
        row.pack(fill=tk.X)
        self._render_current_artwork(row, proposal, file_path)
        self._render_staged_artwork(row, proposal)

    def _render_current_artwork(self, parent, proposal, file_path: str) -> None:
        frame = self._artwork_frame(parent, "Current Embedded")
        current_art = (
            read_front_artwork(file_path) if file_path and os.path.isfile(file_path) else None
        )
        current_art_bytes, _ = current_art or (None, "")
        if not current_art_bytes:
            ttk.Label(frame, text="[No cover art]", foreground="gray").pack(pady=20)
            return
        image = self._load_tk_image_bytes(current_art_bytes)
        if image is None:
            ttk.Label(frame, text="[Image decode error]", foreground="red").pack(pady=10)
            return
        self._preview_images.append(image)
        ttk.Label(frame, image=image).pack(pady=4)
        self._render_current_art_status(frame, proposal, current_art_bytes)

    def _artwork_frame(self, parent, label: str):
        frame = ttk.Frame(parent)
        frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=2)
        ttk.Label(frame, text=label, font=("TkDefaultFont", 8, "bold")).pack(anchor=tk.N)
        return frame

    def _render_current_art_status(self, frame, proposal, artwork: bytes) -> None:
        current_sha = hashlib.sha256(artwork).hexdigest()
        before = getattr(proposal, "artwork_before", None)
        stale = bool(before and getattr(before, "sha256", "") != current_sha)
        ttk.Label(
            frame,
            text="Embedded (Stale)" if stale else "Embedded Cover",
            font=("TkDefaultFont", 7),
            foreground="#d9534f" if stale else "gray",
        ).pack(anchor=tk.N)

    def _render_staged_artwork(self, parent, proposal) -> None:
        frame = self._artwork_frame(parent, "Proposed Replacement")
        artwork = getattr(proposal, "artwork_after", None)
        path = getattr(artwork, "path", None) if artwork else None
        if not path or not os.path.isfile(path):
            ttk.Label(frame, text="[No change]", foreground="gray").pack(pady=20)
            return
        image = self._load_tk_image_file(path)
        if image is None:
            ttk.Label(frame, text="[Decode error]", foreground="red").pack(pady=10)
            return
        self._preview_images.append(image)
        ttk.Label(frame, image=image).pack(pady=4)
        release_id, source_url = (
            getattr(artwork, "release_id", ""),
            getattr(artwork, "source_url", ""),
        )
        label = (
            f"Release: {release_id[:8]}..."
            if release_id
            else ("CAA Source" if source_url else "Proposed")
        )
        ttk.Label(frame, text=label, font=("TkDefaultFont", 7), foreground="green").pack(
            anchor=tk.N
        )


__all__ = ["ReviewDetailsMixin"]
