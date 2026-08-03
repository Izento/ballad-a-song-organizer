"""Compatibility facade for UI-neutral review planners."""

from __future__ import annotations

from .cover_art import download_front_art
from .extractor import extract_track
from .identification import identify
from .media import read_media
from .musicbrainz import enrich_recording, enrich_track
from .planners.analysis import analyze_folder
from .planners.enrichment import (
    plan_metadata_enrichment as _plan_metadata_enrichment,
)
from .planners.extraction import (
    ONLINE_EXTRACTION_WORKERS,
    extract_tracks,
)
from .planners.readiness import (
    coordinate_tag_proposals,
    proposal_identity,
    refresh_rename_readiness,
)
from .planners.rename import plan_renames as _plan_renames
from .planners.tags import plan_tag_updates
from .review_models import path_key as _path_key


_ONLINE_EXTRACTION_WORKERS = ONLINE_EXTRACTION_WORKERS
_proposal_identity = proposal_identity
path_key = _path_key


def _extract_tracks(
    paths,
    strategy,
    acoustid_key,
    progress,
    cancel_event,
):
    return extract_tracks(
        paths,
        strategy,
        acoustid_key,
        progress,
        cancel_event,
        extract=extract_track,
    )


def plan_renames(
    folder_path,
    strategy=None,
    recursive=True,
    lookup=False,
    acoustid_key=None,
    progress=None,
    cancel_event=None,
):
    return _plan_renames(
        folder_path,
        strategy=strategy,
        recursive=recursive,
        lookup=lookup,
        acoustid_key=acoustid_key,
        progress=progress,
        cancel_event=cancel_event,
        extract=extract_track,
        enrich=enrich_track,
        media_reader=read_media,
    )


def plan_metadata_enrichment(
    folder_path,
    *,
    recursive=True,
    acoustid_key=None,
    include_artwork=True,
    include_renames=True,
    progress=None,
    cancel_event=None,
):
    return _plan_metadata_enrichment(
        folder_path,
        recursive=recursive,
        acoustid_key=acoustid_key,
        include_artwork=include_artwork,
        include_renames=include_renames,
        progress=progress,
        cancel_event=cancel_event,
        media_reader=read_media,
        identifier=identify,
        recording_enricher=enrich_recording,
        artwork_download=download_front_art,
    )


__all__ = [
    "analyze_folder",
    "coordinate_tag_proposals",
    "plan_metadata_enrichment",
    "plan_renames",
    "plan_tag_updates",
    "refresh_rename_readiness",
]
