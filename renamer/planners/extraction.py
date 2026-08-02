"""Bounded concurrent extraction for rename planning."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from ..extractor import TrackInfo, extract_track
from .common import ProgressCallback, emit


ONLINE_EXTRACTION_WORKERS = 4
ExtractTrack = Callable[..., TrackInfo]


def _uses_online_extraction(
    strategy: str | None,
    acoustid_key: str | None,
) -> bool:
    return bool(
        acoustid_key
        and strategy not in {"regular", "filename_norm", "musicbrainz"}
    )


def extract_tracks(
    paths: list[str],
    strategy: str | None,
    acoustid_key: str | None,
    progress: ProgressCallback | None,
    cancel_event,
    *,
    extract: ExtractTrack = extract_track,
) -> dict[int, tuple[TrackInfo | None, Exception | None]]:
    """Extract tracks in order while pipelining online fingerprint work."""
    def one(path: str) -> tuple[TrackInfo | None, Exception | None]:
        try:
            return (
                extract(
                    path,
                    strategy=strategy,
                    acoustid_key=acoustid_key,
                ),
                None,
            )
        except (OSError, ValueError) as exc:
            return None, exc

    if not paths or (
        cancel_event is not None and cancel_event.is_set()
    ):
        return {}
    if not _uses_online_extraction(strategy, acoustid_key):
        tracks = {}
        for index, path in enumerate(paths):
            if cancel_event is not None and cancel_event.is_set():
                break
            emit(progress, "extract", index + 1, len(paths), path)
            tracks[index] = one(path)
        return tracks

    worker_count = min(ONLINE_EXTRACTION_WORKERS, len(paths))
    tracks: dict[int, tuple[TrackInfo | None, Exception | None]] = {}
    executor = ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="ballad-fingerprint",
    )
    futures = {}
    next_index = 0
    completed = 0
    try:
        while next_index < len(paths) and len(futures) < worker_count:
            futures[executor.submit(one, paths[next_index])] = next_index
            next_index += 1
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                index = futures.pop(future)
                tracks[index] = future.result()
                completed += 1
                emit(
                    progress,
                    "extract",
                    completed,
                    len(paths),
                    paths[index],
                )
            if cancel_event is not None and cancel_event.is_set():
                for future in futures:
                    future.cancel()
                break
            while next_index < len(paths) and len(futures) < worker_count:
                futures[executor.submit(one, paths[next_index])] = next_index
                next_index += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return tracks


__all__ = ["ONLINE_EXTRACTION_WORKERS", "extract_tracks"]
