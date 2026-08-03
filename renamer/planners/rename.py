"""Read-only rename proposal planning."""

from __future__ import annotations

import os

from ..filename_builder import build_filename
from ..filename_parser import normalize_text, normalize_title_text
from ..media import read_media
from ..musicbrainz import enrich_track
from ..quarantine import is_quarantined
from ..review_models import (
    FileSnapshot,
    RenameProposal,
    canonical_path,
    path_key,
    proposal_id,
)
from ..track_extraction import TrackInfo, extract_track, scan_folder
from ..track_identity import filename_identity_hint
from .parallel_extraction import extract_tracks
from .progress import ProgressCallback, emit, issue
from .readiness import refresh_rename_readiness


def _track_values(track: TrackInfo) -> dict[str, str]:
    if track.is_ocremix:
        return {
            "artist": normalize_title_text(track.game),
            "title": normalize_title_text(track.title),
            "contributors": ", ".join(
                normalize_title_text(remixer)
                for remixer in track.remixers
            ),
        }
    return {
        "artist": track.artist,
        "title": normalize_title_text(track.title),
        "contributors": ", ".join(
            normalize_title_text(feature)
            for feature in track.feat_artists
        ),
    }


def _tag_filename_conflict(path: str, track: TrackInfo) -> str | None:
    if track.strategy != "tag_based":
        return None
    hint = filename_identity_hint(path)
    if hint is None or not track.artist or not track.title:
        return None
    filename_artist, filename_title = hint
    if (
        normalize_text(filename_artist) == normalize_text(track.artist)
        and normalize_text(filename_title) == normalize_text(track.title)
    ):
        return None
    return (
        "Filename identity "
        f"{filename_artist!r} - {filename_title!r} conflicts with embedded "
        f"tags {track.artist!r} - {track.title!r}; automatic rename blocked."
    )


def _resolve_track(
    path: str,
    track: TrackInfo,
    *,
    strategy: str | None,
    lookup: bool,
    acoustid_key: str | None,
    extract,
    enrich,
) -> tuple[TrackInfo | None, bool, str | None]:
    conflict = _tag_filename_conflict(path, track)
    online_conflict = False
    if conflict and acoustid_key:
        identified = extract(
            path,
            strategy=strategy,
            acoustid_key=acoustid_key,
            prefer_acoustid=True,
        )
        if identified.strategy == "acoustid":
            track = identified
            online_conflict = True
            conflict = None
    if conflict:
        return None, online_conflict, conflict
    if lookup and track.needs_lookup:
        track = enrich(track)
    if track.skip_reason:
        return None, online_conflict, track.skip_reason
    if not any((track.artist, track.title, track.game)):
        return None, online_conflict, "No extractable identity"
    return track, online_conflict, None


def _proposal_for_track(
    path: str,
    track: TrackInfo,
    online_conflict: bool,
    *,
    media_reader,
) -> RenameProposal | None:
    media = media_reader(path)
    snapshot = FileSnapshot.capture(
        path,
        tags=media.tags,
        artwork=media.artwork,
        include_hash=True,
    )
    new_name = build_filename(track)
    current_name = os.path.basename(path)
    if current_name == new_name:
        return None
    values = _track_values(track)
    confidence = {
        "tag_based": "high",
        "filename_norm": "medium",
        "acoustid": "medium",
        "musicbrainz": "medium",
    }.get(track.strategy, "low")
    warnings = []
    if track.strategy in {"acoustid", "musicbrainz"}:
        warnings.append(f"Identity came from {track.strategy}.")
    if track.strategy == "acoustid" and track.acoustid_score is not None:
        warnings.append(f"Audio match score: {track.acoustid_score:.3f}.")
    if track.version_warning:
        warnings.append(track.version_warning)
    if online_conflict:
        warnings.append(
            "Embedded tags conflicted with the filename and were not used."
        )
    reason = f"Normalized using {track.strategy or 'automatic'} evidence."
    if track.strategy == "acoustid" and track.acoustid_recording_id:
        reason += (
            f" AcoustID recording {track.acoustid_recording_id} "
            "was retained as evidence."
        )
    new_path = os.path.join(os.path.dirname(path), new_name)
    return RenameProposal(
        id=proposal_id("rename", path, new_path),
        decision_group_id=path_key(path),
        snapshot=snapshot,
        old_path=snapshot.path,
        new_path=canonical_path(new_path),
        current_values={"filename": current_name, **values},
        proposed_values={"filename": new_name, **values},
        confidence=confidence,
        reason=reason,
        warnings=tuple(warnings),
    )


def plan_renames(
    folder_path: str,
    strategy: str | None = None,
    recursive: bool = True,
    lookup: bool = False,
    acoustid_key: str | None = None,
    progress: ProgressCallback | None = None,
    cancel_event=None,
    *,
    scanner=scan_folder,
    extract=extract_track,
    enrich=enrich_track,
    media_reader=read_media,
):
    """Analyze a folder and return rename proposals without mutation."""
    paths = scanner(folder_path, recursive=recursive)
    proposals = []
    issues = []
    extracted = extract_tracks(
        paths,
        strategy,
        acoustid_key,
        progress,
        cancel_event,
        extract=extract,
    )
    for index, path in enumerate(paths, start=1):
        if is_quarantined(path):
            issues.append(
                issue(
                    canonical_path(path),
                    "quarantined",
                    "Skipped rename: match ignored by user quarantine.",
                )
            )
            continue
        result = extracted.get(index - 1)
        if result is None:
            break
        track, extraction_error = result
        if extraction_error is not None:
            issues.append(
                issue(path, "rename", str(extraction_error))
            )
            continue
        emit(progress, "review", index, len(paths), path)
        try:
            if track is None:
                raise ValueError("No extractable identity")
            track, online_conflict, track_error = _resolve_track(
                path,
                track,
                strategy=strategy,
                lookup=lookup,
                acoustid_key=acoustid_key,
                extract=extract,
                enrich=enrich,
            )
            if track_error:
                issues.append(
                    issue(
                        canonical_path(path),
                        "identity-conflict"
                        if "automatic rename blocked" in track_error
                        else "rename",
                        track_error,
                    )
                )
                continue
            proposal = _proposal_for_track(
                path,
                track,
                online_conflict,
                media_reader=media_reader,
            )
            if proposal is not None:
                proposals.append(proposal)
        except (OSError, ValueError) as exc:
            issues.append(issue(canonical_path(path), "rename", str(exc)))
    return refresh_rename_readiness(proposals), issues


__all__ = ["plan_renames"]
