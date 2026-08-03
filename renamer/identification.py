"""Evidence-based identity resolution before release metadata enrichment."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .domain.evidence import RecordingIdentity
from .online.cache import enrichment_cache
from .track_identity import filename_identity_hint, reconcile_online_version

RecordingEvidence = RecordingIdentity


def _cache_key(path: str) -> str:
    stat = Path(path).stat()
    return f"{Path(path).resolve()}|{stat.st_mtime_ns}|{stat.st_size}"


def _existing_recording_id(tags: dict[str, str]) -> str:
    for key in (
        "musicbrainz_recordingid",
        "musicbrainz_trackid",
        "mb_recording_id",
    ):
        if value := tags.get(key, "").strip():
            return value
    return ""


def _cached_evidence(cache, key: str) -> RecordingEvidence | None:
    cached = cache.get("identification", key)
    if cached is None:
        return None
    return RecordingEvidence(
        **{
            **cached,
            "warnings": tuple(cached.get("warnings", ())),
            "provenance": tuple(cached.get("provenance", ())),
        }
    )


def _unidentified_evidence(
    tags: dict[str, str],
    warning: str,
) -> RecordingEvidence:
    return RecordingEvidence(
        artist=tags.get("artist", ""),
        title=tags.get("title", ""),
        warnings=(warning,),
    )


def _matched_evidence(path: str, matched: dict) -> RecordingEvidence:
    online_title = matched.get("title", "")
    recording_id = matched.get("recording_id", "")
    resolution = reconcile_online_version(path, online_title, recording_id)
    hint = filename_identity_hint(path)
    return RecordingEvidence(
        artist=matched.get("artist", "") or (hint[0] if hint else ""),
        title=resolution.title,
        exact_recording_id=resolution.exact_recording_id,
        derived_from_recording_id=resolution.derived_from_recording_id,
        acoustid_score=matched.get("score"),
        confidence="medium" if recording_id else "low",
        warnings=(resolution.warning,) if resolution.warning else (),
        provenance=("AcoustID fingerprint",),
    )


def identify(
    path: str,
    *,
    tags: dict[str, str] | None = None,
    acoustid_key: str | None = None,
    acoustid_lookup: Callable[[str, str], dict | None] | None = None,
) -> RecordingEvidence:
    """Resolve recording evidence without assuming fingerprint equals exact version."""
    source_tags = tags or {}
    if existing_id := _existing_recording_id(source_tags):
        return RecordingEvidence(
            artist=source_tags.get("artist", ""),
            title=source_tags.get("title", ""),
            exact_recording_id=existing_id,
            confidence="high",
            provenance=("embedded MusicBrainz recording ID",),
        )

    if not acoustid_key:
        return _unidentified_evidence(
            source_tags,
            "No online identification key is configured.",
        )
    cache = enrichment_cache()
    key = _cache_key(path)
    if cached := _cached_evidence(cache, key):
        return cached
    if acoustid_lookup is None:
        from .acoustid import lookup

        acoustid_lookup = lookup
    matched = acoustid_lookup(path, acoustid_key)
    if not matched:
        evidence = _unidentified_evidence(
            source_tags,
            "No confident AcoustID recording match.",
        )
        cache.set("identification", key, evidence.to_dict(), ttl_seconds=86400)
        return evidence
    evidence = _matched_evidence(path, matched)
    cache.set("identification", key, evidence.to_dict())
    return evidence


__all__ = ["RecordingEvidence", "identify"]
