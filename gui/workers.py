"""Tk-neutral background operations that emit simple queue events."""

from __future__ import annotations

import queue
import threading

from renamer.apply import apply_review_plan, undo_batch
from renamer.recycle_bin import apply_selected_duplicates
from renamer.review_service import analyze_folder


class BackgroundJobs:
    def __init__(self, events: queue.Queue | None = None) -> None:
        self.events = events or queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None

    @property
    def active(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, stage, current, total, value) -> None:
        path = getattr(value, "path", value) if value else ""
        self.events.put(("progress", stage, current, total, path))

    def _start(self, operation, success_event: str) -> None:
        if self.active:
            raise RuntimeError("A background operation is already running.")
        self.cancel_event = threading.Event()

        def worker() -> None:
            try:
                result = operation()
                payload = result if isinstance(result, tuple) else (result,)
                self.events.put((success_event, *payload))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.events.put(("failed", str(exc)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def organize(
        self,
        folder: str,
        *,
        recursive: bool,
        fingerprint: bool,
        acoustid_key: str | None,
        include_artwork: bool,
        include_duplicates: bool = True,
        include_renames: bool = True,
    ) -> None:
        def operation():
            plan = analyze_folder(
                folder,
                recursive=recursive,
                acoustid_key=acoustid_key,
                fingerprint=fingerprint,
                include_duplicates=include_duplicates,
                enrich_metadata=True,
                include_artwork=include_artwork,
                include_renames=include_renames,
                progress=self._progress,
                cancel_event=self.cancel_event,
            )
            # Nothing is written to disk here. Analysis only prepares a plan;
            # the user must review it and explicitly choose what to apply.
            return plan, []

        self._start(operation, "organize-complete")

    def apply(self, plan, selected_ids) -> None:
        self._start(
            lambda: apply_review_plan(
                plan,
                selected_ids,
                cancel_event=self.cancel_event,
                progress=self._progress,
            ),
            "apply-complete",
        )

    def undo(self, batch_id: str) -> None:
        self._start(lambda: undo_batch(batch_id), "undo-complete")

    def remove_duplicates(self, targets) -> None:
        def operation():
            results = []
            for finding, paths in targets:
                if self.cancel_event.is_set():
                    break
                results.extend(apply_selected_duplicates(finding, paths))
            return results

        self._start(operation, "duplicate-remove-complete")


__all__ = ["BackgroundJobs"]
