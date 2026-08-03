"""Verified metadata, artwork, and filename enrichment planning."""

from __future__ import annotations

import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..acoustid import cache_write_batch
from ..cover_art import download_front_art
from ..domain.evidence import Confidence, weakest_confidence
from ..domain.issues import ReviewIssue
from ..filename_builder import build_filename, split_feat
from ..filename_parser import (
    normalize_text,
    parse_regular_filename,
    split_features,
)
from ..genre_aliases import normalize_genre_list
from ..identification import identify
from ..media import read_media
from ..musicbrainz import enrich_recording
from ..qualifiers import preserve_local_versions, remove_safe_noise
from ..quarantine import is_quarantined
from ..review_models import (
    FileSnapshot,
    RenameProposal,
    TagProposal,
    canonical_path,
    path_key,
    proposal_id,
)
from ..track_extraction import TrackInfo, scan_folder
from ..track_identity import (
    artist_appears_in,
    filename_identity_hint,
    identity_is_recognizable,
    is_placeholder_artist,
)
from .progress import ProgressCallback, emit, issue
from .readiness import refresh_rename_readiness

_SAFE_DERIVATIVE_FIELDS = frozenset(
    {
        "artist",
        "title",
        "composer",
        "writer",
        "lyricist",
        "producer",
        "performer",
    }
)
_PROTECTED_LOCAL_TITLE_MARKERS = frozenset(
    {"cypher", "diss", "freestyle", "unreleased"}
)
_IDENTIFICATION_WORKERS = 4
_ENRICHMENT_WORKERS = 4
_ARTWORK_WORKERS = 3
_LOCAL_CO_ARTIST_RE = re.compile(
    r"\s*(?:&|\band\b|\bx\b|\bvs\.?\b|\bversus\b)\s*",
    re.IGNORECASE,
)


def _features_match(left: str, right: str) -> bool:
    left_normalized = normalize_text(left)
    right_normalized = normalize_text(right)
    left_squashed = re.sub(r"\W+", "", left_normalized)
    right_squashed = re.sub(r"\W+", "", right_normalized)
    if left_squashed == right_squashed:
        return True
    shorter = min(left_squashed, right_squashed, key=len)
    return (
        len(shorter) >= 4
        and SequenceMatcher(
            None,
            left_squashed,
            right_squashed,
        ).ratio()
        >= 0.86
    )


@dataclass(frozen=True)
class EnrichedFilePlan:
    rename: RenameProposal | None = None
    tag: TagProposal | None = None
    issues: tuple[ReviewIssue, ...] = ()


@dataclass(frozen=True)
class IdentifiedFile:
    """One readable file with recording evidence awaiting enrichment."""

    index: int
    path: str
    media: Any
    evidence: Any

    @property
    def recording_id(self) -> str:
        return self.evidence.resolved_recording_id

def _merge_features(
    *groups: tuple[str, ...],
    include_partial_matches: bool = False,
) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        for feature in group:
            normalized = normalize_text(feature)
            if not feature or (include_partial_matches and not normalized):
                continue
            if any(
                _features_match(feature, existing)
                or (
                    include_partial_matches
                    and (
                        normalized in normalize_text(existing)
                        or normalize_text(existing) in normalized
                    )
                )
                for existing in merged
            ):
                continue
            merged.append(feature)
    return tuple(merged)


def _title_with_features(title: str, features: tuple[str, ...]) -> str:
    clean_title, existing_features = split_features(title)
    merged = _merge_features(
        existing_features,
        features,
        include_partial_matches=True,
    )
    if not merged:
        return clean_title
    return f"{clean_title} (feat. {', '.join(merged)})"


def _local_feature_names(
    filename,
    media,
) -> tuple[str, ...]:
    _tag_artist, artist_features = split_features(
        str(media.tags.get("artist") or "")
    )
    _tag_title, title_features = split_features(
        str(media.tags.get("title") or "")
    )
    filename_features = filename.features if filename is not None else ()
    return _merge_features(
        tuple(filename_features),
        tuple(artist_features),
        tuple(title_features),
        include_partial_matches=True,
    )


def _remove_named_features(
    artist: str,
    features: tuple[str, ...],
) -> str:
    """Remove locally explicit feature names from an under-specified credit."""
    cleaned = artist
    for feature in features:
        if not feature:
            continue
        cleaned = re.sub(
            rf"(?<!\w){re.escape(feature)}(?!\w)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(r"\s*(?:&|,)\s*$", "", cleaned)
    cleaned = re.sub(r"^\s*(?:&|,)\s*", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" \t&,-")


def _preserve_local_coartist(values: dict, media) -> dict:
    """Preserve an explicit local collaboration as formatting fallback.

    This does not validate the recording or alter confidence. It only keeps
    an explicit local ``Artist A & Artist B`` layout when the provider
    supplied those same names as blank-join features.
    """
    raw_artist = str(media.tags.get("artist") or "")
    local_artist, explicit_features = split_features(raw_artist)
    if explicit_features or not _LOCAL_CO_ARTIST_RE.search(local_artist):
        return values
    components = [
        component.strip()
        for component in _LOCAL_CO_ARTIST_RE.split(local_artist)
        if component.strip()
    ]
    proposed_artist = str(values.get("artist") or "")
    if len(components) < 2 or not any(
        normalize_text(proposed_artist) == normalize_text(component)
        for component in components
    ):
        return values
    clean_title, title_features = split_features(
        str(values.get("title") or "")
    )
    coartist_features = tuple(
        feature
        for feature in title_features
        if any(
            _features_match(feature, component)
            or normalize_text(feature) in normalize_text(component)
            or normalize_text(component) in normalize_text(feature)
            for component in components[1:]
        )
    )
    if not coartist_features:
        return values
    remaining_features = tuple(
        feature
        for feature in title_features
        if feature not in coartist_features
    )
    values["artist"] = local_artist
    values["title"] = _title_with_features(clean_title, remaining_features)
    return values


def _enriched_values(path, media, evidence, enriched):
    values = dict(enriched.values)
    filename = parse_regular_filename(Path(path).name)
    local_title = (
        filename.title
        if filename is not None
        else media.tags.get("title", "")
    )
    if values.get("title"):
        online_title = preserve_local_versions(
            local_title,
            remove_safe_noise(str(values["title"])),
        )
        values["title"] = _title_with_features(
            online_title,
            _local_feature_names(filename, media),
        )
    values = _preserve_local_coartist(values, media)
    if evidence.is_derivative:
        values = {
            key: value
            for key, value in values.items()
            if key in _SAFE_DERIVATIVE_FIELDS
        }
    return values


def _tag_proposal(
    path: str,
    snapshot: FileSnapshot,
    media,
    after: dict,
    artwork_after,
    evidence,
    enriched,
    warnings: tuple[str, ...],
    confidence: str,
) -> TagProposal | None:
    if after == media.tags and artwork_after is None:
        return None
    return TagProposal(
        id=proposal_id(
            "enrich-tag",
            path,
            {"after": after, "artwork": artwork_after},
        ),
        decision_group_id=path_key(path),
        snapshot=snapshot,
        path=snapshot.path,
        before=media.tags,
        after=after,
        confidence=confidence,
        reason="Enriched from verified MusicBrainz evidence.",
        warnings=warnings,
        artwork_before=media.artwork,
        artwork_after=artwork_after,
        evidence={
            "identification": evidence.to_dict(),
            "musicbrainz": enriched.to_dict(),
        },
    )


def _rename_proposal(
    path: str,
    snapshot: FileSnapshot,
    media,
    after: dict,
    evidence,
    enriched,
    warnings: tuple[str, ...],
    confidence: str,
) -> RenameProposal | None:
    artist = str(after.get("artist") or "")
    title = str(after.get("title") or "")
    if not artist or not title:
        return None
    local_features = _local_feature_names(
        parse_regular_filename(Path(path).name),
        media,
    )
    artist = _remove_named_features(artist, local_features)
    # Feature credits may live in the enriched artist string (an
    # under-specified MusicBrainz artist-credit join), the enriched title
    # (some recordings keep "(feat. X)" inline), or both. Extract from both
    # and merge instead of also reusing the *original* filename's feature
    # list, which would double up on the same name.
    artist, artist_features = split_feat(artist)
    title, title_features = split_feat(title)
    feat_artists = _merge_features(artist_features, title_features)
    track = TrackInfo(
        path=path,
        ext=Path(path).suffix,
        artist=artist,
        title=title,
        feat_artists=feat_artists,
        strategy="musicbrainz",
    )
    new_name = build_filename(track)
    if Path(path).name == new_name:
        return None
    return RenameProposal(
        id=proposal_id("enrich-rename", path, new_name),
        decision_group_id=path_key(path),
        snapshot=snapshot,
        old_path=snapshot.path,
        new_path=canonical_path(str(Path(path).with_name(new_name))),
        current_values={"filename": Path(path).name, **media.tags},
        proposed_values={"filename": new_name, **after},
        confidence=confidence,
        reason="Filename aligned with enriched MusicBrainz metadata.",
        warnings=warnings,
        evidence={
            "identification": evidence.to_dict(),
            "musicbrainz": enriched.to_dict(),
        },
    )


def _local_identities(path: str, media) -> tuple[tuple[str, str], ...]:
    """Each claim the file itself makes about what song it is."""
    hint = filename_identity_hint(path) or ("", "")
    claims = (
        (str(media.tags.get("artist") or ""), str(media.tags.get("title") or "")),
        hint,
    )
    return tuple(claim for claim in claims if claim[0] or claim[1])


def _identity_warning(path: str, media, values: dict) -> str:
    """Warn when an enriched identity no longer resembles the local file.

    Providers can't catch this themselves: a wrong recording ID resolves to
    perfectly clean metadata for the wrong song. Any single point of
    agreement clears the match -- the file's tags, its parsed filename, or
    the matched artist simply being named somewhere in the filename. Each
    is often the only intact evidence left, since a stale tag or a
    label-prefixed name is exactly what enrichment is here to fix.
    """
    proposed_artist = str(values.get("artist") or "")
    proposed_title = str(values.get("title") or "")
    if not proposed_artist and not proposed_title:
        return ""
    claims = _local_identities(path, media)
    label_prefixed = Path(path).stem.count(" - ") >= 2
    if not claims or (
        label_prefixed and artist_appears_in(Path(path).stem, proposed_artist)
    ):
        return ""
    if any(
        identity_is_recognizable(
            local_artist=artist,
            local_title=title,
            proposed_artist=proposed_artist,
            proposed_title=proposed_title,
        )
        for artist, title in claims
    ):
        return ""
    local_artist, local_title = claims[0]
    return (
        f"Identity mismatch: this file says \"{local_artist} - {local_title}\" "
        f"but the matched recording is \"{proposed_artist} - {proposed_title}\". "
        "Confirm the match before applying."
    )


def _placeholder_identity_warning(values: dict) -> str:
    proposed_artist = str(values.get("artist") or "")
    if not is_placeholder_artist(proposed_artist):
        return ""
    return (
        f'Placeholder identity: the provider proposed "{proposed_artist}" as '
        "the artist. Ballad will not apply placeholder artist metadata."
    )


def _protected_identity_warning(path: str, media, values: dict) -> str:
    proposed_artist = str(values.get("artist") or "")
    proposed_title = str(values.get("title") or "")
    proposed_markers = {
        marker
        for marker in _PROTECTED_LOCAL_TITLE_MARKERS
        if re.search(rf"\b{re.escape(marker)}\b", normalize_text(proposed_title))
    }
    for local_artist, local_title in _local_identities(path, media):
        local_markers = {
            marker
            for marker in _PROTECTED_LOCAL_TITLE_MARKERS
            if re.search(rf"\b{re.escape(marker)}\b", normalize_text(local_title))
        }
        if not local_markers:
            continue
        primary_artist_survives = (
            not local_artist
            or artist_appears_in(proposed_artist, local_artist)
        )
        if local_markers <= proposed_markers and primary_artist_survives:
            continue
        markers = ", ".join(sorted(local_markers))
        return (
            f"Protected local identity: this file is labeled as {markers}, "
            "but the proposed primary artist/title does not preserve that "
            "identity. Ballad will not apply this match."
        )
    return ""


def _identification_failure(path: str, message: str) -> EnrichedFilePlan:
    return EnrichedFilePlan(
        issues=(
            issue(
                canonical_path(path),
                "metadata-identification",
                message,
            ),
        )
    )


def _identify_file(
    index: int,
    path: str,
    *,
    acoustid_key: str | None,
    media_reader,
    identifier,
) -> IdentifiedFile | EnrichedFilePlan:
    if is_quarantined(path):
        return EnrichedFilePlan(
            issues=(
                issue(
                    canonical_path(path),
                    "quarantined",
                    "Skipped identification: match ignored by user quarantine.",
                ),
            )
        )
    media = media_reader(path)
    if not media.usable:
        return EnrichedFilePlan(
            issues=(
                issue(
                    canonical_path(path),
                    "metadata-enrichment",
                    f"{media.status}: {media.error or 'cannot read media'}",
                ),
            )
        )
    try:
        evidence = identifier(
            path,
            tags=media.tags,
            acoustid_key=acoustid_key,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return _identification_failure(path, str(exc))
    if not evidence.resolved_recording_id:
        return _identification_failure(
            path,
            "; ".join(evidence.warnings) or "No recording match.",
        )
    return IdentifiedFile(index, path, media, evidence)


def _run_identification(
    paths: list[str],
    *,
    acoustid_key: str | None,
    progress,
    cancel_event,
    media_reader,
    identifier,
) -> dict[int, IdentifiedFile | EnrichedFilePlan]:
    """Read and identify files concurrently; providers retain their own limits."""
    if not paths:
        return {}
    results: dict[int, IdentifiedFile | EnrichedFilePlan] = {}
    worker_count = min(_IDENTIFICATION_WORKERS, len(paths))

    def work(index: int, path: str):
        try:
            return index, _identify_file(
                index,
                path,
                acoustid_key=acoustid_key,
                media_reader=media_reader,
                identifier=identifier,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            return index, _identification_failure(path, str(exc))

    with cache_write_batch(), ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="ballad-identify",
    ) as executor:
        pending: dict[object, int] = {}
        next_index = 0
        completed = 0
        while next_index < len(paths) and len(pending) < worker_count:
            pending[executor.submit(work, next_index, paths[next_index])] = next_index
            next_index += 1
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                result_index, result = future.result()
                results[result_index] = result
                completed += 1
                emit(
                    progress,
                    "enrich-identify",
                    completed,
                    len(paths),
                    paths[index],
                )
            if cancel_event is not None and cancel_event.is_set():
                for future in pending:
                    future.cancel()
                break
            while next_index < len(paths) and len(pending) < worker_count:
                pending[executor.submit(work, next_index, paths[next_index])] = next_index
                next_index += 1
    return results


def _local_evidence(candidates: list[IdentifiedFile]) -> dict[str, object]:
    """Select the richest local release hint for a shared recording."""
    return dict(
        max(
            candidates,
            key=lambda candidate: sum(
                bool(value) for value in candidate.media.tags.values()
            ),
        ).media.tags
    )


def _recording_groups(
    candidates: list[IdentifiedFile],
) -> dict[str, list[IdentifiedFile]]:
    groups: dict[str, list[IdentifiedFile]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.recording_id, []).append(candidate)
    return groups


def _enrichment_future_result(future, pending):
    """Return one enrichment result, cancelling siblings on failure."""
    try:
        return future.result()
    except Exception:  # pylint: disable=broad-exception-caught
        for pending_future in pending:
            pending_future.cancel()
        raise


def _enrich_recordings(
    candidates: list[IdentifiedFile],
    *,
    progress,
    cancel_event,
    recording_enricher,
) -> dict[str, object | None]:
    """Fetch each unique recording once with concurrent request preparation."""
    groups = _recording_groups(candidates)
    if not groups:
        return {}
    tasks = [
        (recording_id, grouped[0].path, _local_evidence(grouped))
        for recording_id, grouped in groups.items()
    ]
    results: dict[str, object | None] = {}
    worker_count = min(_ENRICHMENT_WORKERS, len(tasks))

    def work(recording_id: str, _path: str, local_evidence: dict[str, object]):
        return recording_id, recording_enricher(
            recording_id,
            local_evidence=local_evidence,
        )

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="ballad-musicbrainz",
    ) as executor:
        futures = {
            executor.submit(work, *task): task
            for task in tasks
        }
        completed = 0
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                recording_id, path, _local_tags = futures.pop(future)
                result_id, result = _enrichment_future_result(future, futures)
                results[result_id] = result
                completed += 1
                emit(
                    progress,
                    "enrich-musicbrainz",
                    completed,
                    len(tasks),
                    path,
                )
            if cancel_event is not None and cancel_event.is_set():
                for future in futures:
                    future.cancel()
                break
    return results


def _artwork_requests(
    candidates: list[IdentifiedFile],
    enrichments: dict[str, object | None],
    include_artwork: bool,
) -> dict[str, str]:
    if not include_artwork:
        return {}
    requests: dict[str, str] = {}
    for candidate in candidates:
        enriched = enrichments.get(candidate.recording_id)
        if (
            enriched is None
            or candidate.evidence.is_derivative
            or candidate.media.artwork is not None
            or not enriched.release_id
        ):
            continue
        requests.setdefault(enriched.release_id, candidate.path)
    return requests


def _download_artwork(
    requests: dict[str, str],
    *,
    progress,
    cancel_event,
    artwork_download,
) -> dict[str, dict | None]:
    """Download each selected release cover once for all its tracks."""
    if not requests:
        return {}
    results: dict[str, dict | None] = {}
    worker_count = min(_ARTWORK_WORKERS, len(requests))

    def work(release_id: str):
        artwork = artwork_download(release_id)
        return release_id, artwork.to_dict() if artwork is not None else None

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="ballad-artwork",
    ) as executor:
        futures = {
            executor.submit(work, release_id): (release_id, path)
            for release_id, path in requests.items()
        }
        completed = 0
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                release_id, path = futures.pop(future)
                result_id, artwork = future.result()
                results[result_id] = artwork
                completed += 1
                emit(
                    progress,
                    "enrich-artwork",
                    completed,
                    len(requests),
                    path,
                )
            if cancel_event is not None and cancel_event.is_set():
                for future in futures:
                    future.cancel()
                break
    return results


def _missing_enrichment_plan(path: str) -> EnrichedFilePlan:
    return EnrichedFilePlan(
        issues=(
            issue(
                canonical_path(path),
                "metadata-enrichment",
                "MusicBrainz did not provide metadata for this recording.",
            ),
        )
    )


def _merged_after(media, values: dict) -> dict:
    """Merge enriched values, then normalize the resulting genre field."""
    after = {**media.tags, **values}
    if after.get("genre"):
        # Applied on the merged result, not just fresh MusicBrainz values,
        # so a local "Rap" is consolidated when MusicBrainz has no genre.
        after["genre"] = normalize_genre_list(list(after["genre"]))
    return after


def _planned_artwork(media, evidence, enriched, artwork_by_release):
    if evidence.is_derivative or media.artwork is not None:
        return None
    return artwork_by_release.get(enriched.release_id)


def _planning_warnings(candidate, enriched, values, after):
    path = candidate.path
    media = candidate.media
    evidence = candidate.evidence
    warnings = tuple((*evidence.warnings, *enriched.warnings))
    if evidence.is_derivative:
        warnings += (
            "Local derivative: source recording/release IDs and artwork "
            "were not written.",
        )
    identity_warning = _identity_warning(path, media, values)
    if identity_warning:
        warnings += (identity_warning,)
    safety_warnings = tuple(
        warning
        for warning in (
            _placeholder_identity_warning(after),
            _protected_identity_warning(path, media, after),
        )
        if warning
    )
    return warnings + safety_warnings, identity_warning, safety_warnings


def _planned_confidence(evidence, enriched, identity_warning, safety_warnings) -> str:
    if identity_warning or safety_warnings:
        return Confidence.LOW.value
    return weakest_confidence(evidence.confidence, enriched.confidence).value


def _plan_identified_file(
    candidate: IdentifiedFile,
    enriched,
    artwork_by_release: dict[str, dict | None],
) -> EnrichedFilePlan:
    path, media, evidence = candidate.path, candidate.media, candidate.evidence
    if enriched is None:
        return _missing_enrichment_plan(path)
    values = _enriched_values(path, media, evidence, enriched)
    after = _merged_after(media, values)
    artwork_after = _planned_artwork(
        media,
        evidence,
        enriched,
        artwork_by_release,
    )
    snapshot = FileSnapshot.capture(
        path,
        tags=media.tags,
        artwork=media.artwork,
        include_hash=True,
    )
    warnings, identity_warning, safety_warnings = _planning_warnings(
        candidate,
        enriched,
        values,
        after,
    )
    confidence = _planned_confidence(
        evidence,
        enriched,
        identity_warning,
        safety_warnings,
    )
    return EnrichedFilePlan(
        tag=_tag_proposal(
            path,
            snapshot,
            media,
            after,
            artwork_after,
            evidence,
            enriched,
            warnings,
            confidence,
        ),
        rename=_rename_proposal(
            path,
            snapshot,
            media,
            after,
            evidence,
            enriched,
            warnings,
            confidence,
        ),
    )


def _identified_candidates(identified):
    candidates: list[IdentifiedFile] = []
    issues: list[ReviewIssue] = []
    for index in sorted(identified):
        result = identified[index]
        if isinstance(result, IdentifiedFile):
            candidates.append(result)
        else:
            issues.extend(result.issues)
    return candidates, issues


def _plans_for_candidates(
    candidates: list[IdentifiedFile],
    enrichments: dict[str, object | None],
    artwork_by_release: dict[str, dict | None],
    *,
    include_renames: bool,
):
    renames = []
    tags = []
    issues = []
    for candidate in candidates:
        result = _plan_identified_file(
            candidate,
            enrichments.get(candidate.recording_id),
            artwork_by_release,
        )
        if include_renames and result.rename is not None:
            renames.append(result.rename)
        if result.tag is not None:
            tags.append(result.tag)
        issues.extend(result.issues)
    return renames, tags, issues


def plan_metadata_enrichment(
    folder_path: str,
    *,
    recursive: bool = True,
    acoustid_key: str | None = None,
    include_artwork: bool = True,
    include_renames: bool = True,
    progress: ProgressCallback | None = None,
    cancel_event=None,
    scanner=scan_folder,
    media_reader=read_media,
    identifier=identify,
    recording_enricher=enrich_recording,
    artwork_download=download_front_art,
):
    paths = scanner(folder_path, recursive=recursive)
    identified = _run_identification(
        paths,
        acoustid_key=acoustid_key,
        progress=progress,
        cancel_event=cancel_event,
        media_reader=media_reader,
        identifier=identifier,
    )
    candidates, issues = _identified_candidates(identified)
    if cancel_event is not None and cancel_event.is_set():
        return refresh_rename_readiness([]), [], issues
    enrichments = _enrich_recordings(
        candidates,
        progress=progress,
        cancel_event=cancel_event,
        recording_enricher=recording_enricher,
    )
    if cancel_event is not None and cancel_event.is_set():
        return refresh_rename_readiness([]), [], issues
    artwork_by_release = _download_artwork(
        _artwork_requests(candidates, enrichments, include_artwork),
        progress=progress,
        cancel_event=cancel_event,
        artwork_download=artwork_download,
    )
    if cancel_event is not None and cancel_event.is_set():
        return refresh_rename_readiness([]), [], issues
    renames, tags, planning_issues = _plans_for_candidates(
        candidates,
        enrichments,
        artwork_by_release,
        include_renames=include_renames,
    )
    issues.extend(planning_issues)
    return refresh_rename_readiness(renames), tags, issues


__all__ = ["EnrichedFilePlan", "plan_metadata_enrichment"]
