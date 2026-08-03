"""Top-level composition of independent review planners."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..dedup import (
    RegularTrack,
    analyze_duplicates,
    analyze_regular_duplicates,
    collect_tracks,
)
from ..domain.issues import ReviewIssue
from ..review_models import ReviewPlan, canonical_path, path_key
from ..tag_audit import plan_tag_updates
from .enrichment import plan_metadata_enrichment
from .progress import ProgressCallback, emit, issue
from .readiness import coordinate_tag_proposals
from .rename import plan_renames


def _duplicate_findings(
    folder_path: str,
    *,
    recursive: bool,
    fingerprint: bool,
    progress,
    cancel_event,
    identity_overrides: dict[str, tuple[str, str]] | None = None,
):
    if cancel_event is not None and cancel_event.is_set():
        return [], []
    try:
        findings = analyze_duplicates(
            folder_path,
            recursive=recursive,
            progress=(
                lambda current, total, path: emit(
                    progress,
                    "duplicate-audit",
                    current,
                    total,
                    path,
                )
            )
            if progress
            else None,
            cancel_event=cancel_event,
            fingerprint=fingerprint,
            identity_overrides=identity_overrides,
        )
        return findings, []
    except (OSError, ValueError) as exc:
        return [], [
            issue(
                canonical_path(folder_path),
                "duplicate-audit",
                str(exc),
            )
        ]


def _collect_duplicate_tracks(
    folder_path: str,
    *,
    recursive: bool,
    fingerprint: bool,
    progress,
    cancel_event,
) -> tuple[list[RegularTrack], list[ReviewIssue]]:
    """Scan and hash every file -- the disk/CPU-bound half of dedup.

    Meant to run on a background thread alongside `plan_metadata_enrichment`,
    which is mostly idle time spent waiting on MusicBrainz's rate limit.
    Grouping the collected tracks into findings happens separately, once
    enrichment's results are available to use as identity overrides.
    """
    if cancel_event is not None and cancel_event.is_set():
        return [], []
    try:
        tracks = collect_tracks(
            folder_path,
            recursive,
            progress=(
                lambda current, total, path: emit(
                    progress,
                    "duplicate-audit",
                    current,
                    total,
                    path,
                )
            )
            if progress
            else None,
            cancel_event=cancel_event,
            fingerprint=fingerprint,
        )
        return tracks, []
    except (OSError, ValueError) as exc:
        return [], [
            issue(
                canonical_path(folder_path),
                "duplicate-audit",
                str(exc),
            )
        ]


def _grouped_duplicate_findings(
    folder_path: str,
    tracks: list[RegularTrack],
    *,
    identity_overrides: dict[str, tuple[str, str]] | None,
):
    """Group pre-collected tracks into findings; no disk I/O happens here."""
    try:
        findings = analyze_regular_duplicates(
            tracks=tracks,
            identity_overrides=identity_overrides,
        )
        return findings, []
    except (OSError, ValueError) as exc:
        return [], [
            issue(
                canonical_path(folder_path),
                "duplicate-audit",
                str(exc),
            )
        ]


def _run_enrichment(
    folder_path: str,
    *,
    recursive: bool,
    acoustid_key: str | None,
    include_artwork: bool,
    include_renames: bool = True,
    progress,
    cancel_event,
):
    return plan_metadata_enrichment(
        folder_path,
        recursive=recursive,
        acoustid_key=acoustid_key,
        include_artwork=include_artwork,
        include_renames=include_renames,
        progress=progress,
        cancel_event=cancel_event,
    )


def _identity_overrides(renames, tags) -> dict[str, tuple[str, str]]:
    """Map each enriched file to its MusicBrainz-verified (artist, title).

    Near-duplicate matching otherwise has to guess identity from the same
    unreliable filenames this tool exists to fix -- two copies of one song
    with completely different bad names would never group together. Files
    enrichment left untouched (because their tags were already correct)
    aren't included here; their existing tags are already a fine fallback
    for `_core_key`.
    """
    overrides: dict[str, tuple[str, str]] = {}
    for tag in tags:
        artist = str(tag.after.get("artist") or "").strip()
        title = str(tag.after.get("title") or "").strip()
        if artist and title:
            overrides[path_key(tag.path)] = (artist, title)
    for rename in renames:
        artist = str(rename.proposed_values.get("artist") or "").strip()
        title = str(rename.proposed_values.get("title") or "").strip()
        if artist and title:
            overrides[path_key(rename.old_path)] = (artist, title)
    return overrides


def _enriched_results(
    folder_path: str,
    *,
    recursive: bool,
    acoustid_key: str | None,
    include_duplicates: bool,
    fingerprint: bool,
    include_artwork: bool,
    include_renames: bool,
    progress,
    cancel_event,
):
    """Run enrichment with duplicate collection when both are requested."""
    if not include_duplicates:
        renames, tags, issues = _run_enrichment(
            folder_path,
            recursive=recursive,
            acoustid_key=acoustid_key,
            include_artwork=include_artwork,
            include_renames=include_renames,
            progress=progress,
            cancel_event=cancel_event,
        )
        return renames, tags, issues, [], []
    # Enrichment is mostly idle time waiting on MusicBrainz's rate limit;
    # hashing every file for dedup is pure disk/CPU work with no waiting.
    with ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="ballad-dedup-collect",
    ) as executor:
        collect_future = executor.submit(
            _collect_duplicate_tracks,
            folder_path,
            recursive=recursive,
            fingerprint=fingerprint,
            progress=progress,
            cancel_event=cancel_event,
        )
        renames, tags, issues = _run_enrichment(
            folder_path,
            recursive=recursive,
            acoustid_key=acoustid_key,
            include_artwork=include_artwork,
            include_renames=include_renames,
            progress=progress,
            cancel_event=cancel_event,
        )
        tracks, collect_issues = collect_future.result()
    duplicate_findings, duplicate_issues = _grouped_duplicate_findings(
        folder_path,
        tracks,
        identity_overrides=_identity_overrides(renames, tags),
    )
    return renames, tags, issues, duplicate_findings, [
        *collect_issues,
        *duplicate_issues,
    ]


def _enriched_review_plan(
    folder_path: str,
    *,
    recursive: bool,
    acoustid_key: str | None,
    include_duplicates: bool,
    fingerprint: bool,
    include_artwork: bool,
    include_renames: bool,
    progress,
    cancel_event,
) -> ReviewPlan:
    """Build a review plan from MusicBrainz-backed enrichment."""
    renames, tags, issues, duplicate_findings, duplicate_issues = _enriched_results(
        folder_path,
        recursive=recursive,
        acoustid_key=acoustid_key,
        include_duplicates=include_duplicates,
        fingerprint=fingerprint,
        include_artwork=include_artwork,
        include_renames=include_renames,
        progress=progress,
        cancel_event=cancel_event,
    )
    return ReviewPlan.create(
        root=folder_path,
        recursive=recursive,
        rename_proposals=renames,
        tag_proposals=tags,
        duplicate_findings=duplicate_findings,
        issues=[*issues, *duplicate_issues],
    )


def _standard_review_plan(
    folder_path: str,
    *,
    strategy: str | None,
    recursive: bool,
    lookup: bool,
    acoustid_key: str | None,
    include_duplicates: bool,
    fingerprint: bool,
    include_renames: bool,
    progress,
    cancel_event,
) -> ReviewPlan:
    """Build a review plan from local tags and filename extraction."""
    renames, rename_issues = (
        plan_renames(
            folder_path,
            strategy=strategy,
            recursive=recursive,
            lookup=lookup,
            acoustid_key=acoustid_key,
            progress=progress,
            cancel_event=cancel_event,
        )
        if include_renames
        else ([], [])
    )
    tags, tag_issues = plan_tag_updates(
        folder_path,
        recursive=recursive,
        progress=progress,
        cancel_event=cancel_event,
    )
    tags, coordination_issues, synchronized = coordinate_tag_proposals(renames, tags)
    tag_issues = [
        item
        for item in tag_issues
        if path_key(item.get("path", "")) not in synchronized
    ]
    tag_issues.extend(coordination_issues)
    duplicate_findings = []
    duplicate_issues: list[ReviewIssue] = []
    if include_duplicates:
        duplicate_findings, duplicate_issues = _duplicate_findings(
            folder_path,
            recursive=recursive,
            fingerprint=fingerprint,
            progress=progress,
            cancel_event=cancel_event,
        )
    return ReviewPlan.create(
        root=folder_path,
        recursive=recursive,
        rename_proposals=renames,
        tag_proposals=tags,
        duplicate_findings=duplicate_findings,
        issues=[*rename_issues, *tag_issues, *duplicate_issues],
    )


def analyze_folder(
    folder_path: str,
    strategy: str | None = None,
    recursive: bool = True,
    lookup: bool = False,
    acoustid_key: str | None = None,
    include_duplicates: bool = True,
    fingerprint: bool = False,
    enrich_metadata: bool = False,
    include_artwork: bool = True,
    include_renames: bool = True,
    progress: ProgressCallback | None = None,
    cancel_event=None,
) -> ReviewPlan:
    """Build one immutable review plan for the selected root."""
    if enrich_metadata:
        return _enriched_review_plan(
            folder_path,
            recursive=recursive,
            acoustid_key=acoustid_key,
            include_duplicates=include_duplicates,
            fingerprint=fingerprint,
            include_artwork=include_artwork,
            include_renames=include_renames,
            progress=progress,
            cancel_event=cancel_event,
        )
    return _standard_review_plan(
        folder_path,
        strategy=strategy,
        recursive=recursive,
        lookup=lookup,
        acoustid_key=acoustid_key,
        include_duplicates=include_duplicates,
        fingerprint=fingerprint,
        include_renames=include_renames,
        progress=progress,
        cancel_event=cancel_event,
    )


__all__ = ["analyze_folder"]
