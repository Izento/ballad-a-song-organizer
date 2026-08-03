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


class ReviewDetailsMixin:
    """Show one proposal's metadata, evidence, and cover art."""

    def _on_tree_select(self, tree_name: str, _event=None) -> None:
        tree = self.trees.get(tree_name)
        if not tree or not tree.selection():
            return
        row = tree.selection()[0]
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
        file_path = self._render_details_summary(container, proposal)
        self._render_details_warnings(container, proposal)
        self._render_metadata_proposal(container, proposal)
        self._render_provider_evidence(container, proposal)
        self._render_artwork_inspection(container, proposal, file_path)

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
            text=Path(file_path).name or "Unknown file",
            font=("TkDefaultFont", 10, "bold"),
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
        box = ttk.Labelframe(container, text="Metadata Proposal", padding=6)
        box.pack(fill=tk.X, pady=(0, 8))
        for label, before, after in metadata_differences(proposal):
            if before or after:
                self._add_metadata_row(box, label, before, after)
        if hasattr(proposal, "old_path") and hasattr(proposal, "new_path"):
            self._add_filename_row(box, proposal)

    def _add_metadata_row(self, parent, label: str, before: object, after: object) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=f"{label}:", font=("TkDefaultFont", 9, "bold"), width=8).pack(
            side=tk.LEFT
        )
        value = f"{before or '(none)'}  ➔  {after or '(none)'}"
        ttk.Label(
            row,
            text=value if before != after else f"{after or '(unchanged)'}",
            wraplength=230,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT)

    def _add_filename_row(self, parent, proposal) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="Filename:", font=("TkDefaultFont", 9, "bold"), width=8).pack(
            side=tk.LEFT
        )
        old_name, new_name = Path(proposal.old_path).name, Path(proposal.new_path).name
        ttk.Label(
            row,
            text=f"{old_name}\n➔ {new_name}" if old_name != new_name else new_name,
            wraplength=230,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT)

    def _render_provider_evidence(self, container, proposal) -> None:
        identification, musicbrainz = proposal_evidence(proposal)
        box = ttk.Labelframe(container, text="Provider Evidence", padding=6)
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
        for label, key in (("MB Recording ID", "recording_id"), ("MB Release ID", "release_id")):
            if evidence.get(key):
                ttk.Label(
                    parent,
                    text=f"{label}:\n{evidence[key]}",
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
