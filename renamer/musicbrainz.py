"""Rate-limited MusicBrainz lookup helpers."""

from __future__ import annotations

import importlib.util
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from .domain.evidence import Confidence
from .domain.metadata import CanonicalMetadata
from .filename_parser import normalize_text, split_features
from .online import RateLimiter
from .online.cache import enrichment_cache
from .track_identity import prefer_latin_text

if TYPE_CHECKING:
    from .track_extraction import TrackInfo

_RELEASE_CACHE: dict[str, list] = {}
_CACHE_REQUEST_LOCK = threading.Lock()
_IN_FLIGHT_REQUESTS: dict[tuple[str, str], threading.Lock] = {}


_REQUEST_LIMITER = RateLimiter(1.1)


@dataclass(frozen=True)
class ReleaseCandidate:
    """A release that contains a recording, scored against local evidence."""

    release_id: str
    title: str
    date: str = ""
    status: str = ""
    country: str = ""
    language: str = ""
    script: str = ""
    release_group_type: str = ""
    score: int = 0
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class EnrichmentResult:
    """Verified recording/release fields safe to merge into local metadata."""

    recording_id: str
    values: CanonicalMetadata = field(default_factory=CanonicalMetadata)
    release_id: str = ""
    release_group_id: str = ""
    confidence: Confidence = Confidence.LOW
    warnings: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", CanonicalMetadata.coerce(self.values))
        object.__setattr__(self, "confidence", Confidence(self.confidence))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "provenance", tuple(self.provenance))

    if TYPE_CHECKING:

        def __init__(  # noqa: PLR0917
            self,
            recording_id: str,
            values: CanonicalMetadata | Mapping[str, object] | None = None,
            release_id: str = "",
            release_group_id: str = "",
            confidence: Confidence | str = Confidence.LOW,
            warnings: Iterable[str] = (),
            provenance: Iterable[str] = (),
        ) -> None: ...

    def to_dict(self) -> dict[str, object]:
        return {
            "recording_id": self.recording_id,
            "values": self.values.to_dict(),
            "release_id": self.release_id,
            "release_group_id": self.release_group_id,
            "confidence": self.confidence.value,
            "warnings": list(self.warnings),
            "provenance": list(self.provenance),
        }


def _available() -> bool:
    return importlib.util.find_spec("musicbrainzngs") is not None


def is_available() -> bool:
    """Return whether the optional MusicBrainz client can be imported."""
    return _available()


def _mb():
    import musicbrainzngs  # pylint: disable=import-error

    musicbrainzngs.set_useragent(
        "Ballad",
        "1.0",
        "https://github.com/Izento/ballad-a-song-organizer",
    )
    return musicbrainzngs


def _rate_limit() -> None:
    _REQUEST_LIMITER.wait()


def _populate_album_track_cache(
    cache_key: str,
    album: str,
    artist_hint: str,
) -> None:
    mb = _mb()
    try:
        _rate_limit()
        query_kwargs = {"release": album, "limit": 3}
        if artist_hint:
            query_kwargs["artist"] = artist_hint
        result = mb.search_releases(**query_kwargs)
        releases = result.get("release-list", [])
        if not releases:
            _RELEASE_CACHE[cache_key] = []
            return
        candidate, _warnings = select_release(
            releases,
            {"album": album, "album_artist": artist_hint},
        )
        if candidate is None:
            _RELEASE_CACHE[cache_key] = []
            return
        _rate_limit()
        release = mb.get_release_by_id(
            candidate.release_id,
            includes=["recordings"],
        )
        _RELEASE_CACHE[cache_key] = [
            track
            for medium in release["release"].get("medium-list", [])
            for track in medium.get("track-list", [])
        ]
    except (mb.MusicBrainzError, KeyError, TypeError, ValueError):
        _RELEASE_CACHE[cache_key] = []


def _track_by_number(
    tracks: list,
    track_num: int,
    artist_hint: str,
) -> dict | None:
    for track in tracks:
        if int(track.get("position", -1)) == track_num:
            rec = track.get("recording", {})
            artist_credits = rec.get("artist-credit", [])
            provider_artist = _artist_credit_name(artist_credits) if artist_credits else ""
            track_artist = _artist_credit_name(track.get("artist-credit"))
            artist_name = prefer_latin_text(
                artist_hint,
                track_artist or provider_artist or artist_hint,
            )
            provider_title = str(rec.get("title") or "")
            track_title = str(track.get("title") or "")
            return {
                "artist": artist_name,
                "title": prefer_latin_text(provider_title, track_title or provider_title),
            }
    return None


def lookup_track_by_album(album: str, track_num: int, artist_hint: str = "") -> dict | None:
    """
    Find a track title by album name and track number.
    Returns {'artist': str, 'title': str} or None if not found.
    Used for Hikaru Utada style folders where files are "01 Track 1.mp3".

    Caches the full track list per album so only one API call is made per album
    regardless of how many tracks are looked up.
    """
    if not _available():
        return None
    cache_key = f"{album}||{artist_hint}"
    if cache_key not in _RELEASE_CACHE:
        _populate_album_track_cache(cache_key, album, artist_hint)
    return _track_by_number(_RELEASE_CACHE[cache_key], track_num, artist_hint)


def lookup_ocremix_remixers(game: str, song_title: str) -> list[str] | None:
    """
    Find OC ReMix remixer names by game and song title.
    Returns a list of remixer names, or None if nothing found.
    Used for Gamer's Delight where the old format has no remixer in the metadata.
    """
    if not _available():
        return None

    mb = _mb()
    try:
        _rate_limit()
        # Search for the recording on OC ReMix's MusicBrainz label
        result = mb.search_recordings(
            recording=song_title,
            artist=game,
            limit=5,
        )
        recordings = result.get("recording-list", [])

        for rec in recordings:
            title = rec.get("title", "")
            if "OC ReMix" not in title and song_title.lower() not in title.lower():
                continue
            artist_credits = rec.get("artist-credit", [])
            names = [
                c["artist"]["name"] for c in artist_credits if isinstance(c, dict) and "artist" in c
            ]
            if names:
                return names

    except (mb.MusicBrainzError, KeyError, TypeError, ValueError):
        return None

    return None


_FEATURE_JOIN_RE = re.compile(
    r"\b(?:feat(?:uring)?|ft)\.?\b|\bwith\b",
    re.IGNORECASE,
)
_CO_ARTIST_JOIN_RE = re.compile(
    r"^\s*(?:&|and|x|vs\.?|versus)\s*$",
    re.IGNORECASE,
)


def _credit_name(credit: dict) -> str:
    artist = credit.get("artist") or {}
    return str(credit.get("name") or artist.get("name") or "").strip()


def _feature_relation_names(relations: list | None) -> set[str]:
    """Return artists explicitly marked as vocal/guest contributors."""
    names: set[str] = set()
    for relation in relations or []:
        if not isinstance(relation, dict):
            continue
        relation_type = str(relation.get("type") or "").casefold()
        attributes = {str(value).casefold() for value in relation.get("attribute-list", [])}
        is_feature = relation_type in {"vocal", "featured artist"} or bool(
            attributes
            & {
                "additional",
                "background vocals",
                "guest",
                "lead vocals",
                "vocals",
            }
        )
        if not is_feature:
            continue
        artist = relation.get("artist") or {}
        name = str(artist.get("name") or "").strip()
        if name:
            names.add(normalize_text(name))
    return names


def _artist_credit_identity(
    artist_credits: list | None,
    relations: list | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Split a credit into a primary artist and conservative features.

    MusicBrainz frequently returns several credited artists with blank
    ``joinphrase`` values. Blank does not mean ``&``. If recording
    relations identify a vocalist, use that role and discard unrelated
    extra credits (often remix/production credits). Otherwise, retain
    additional blank-join credits as features rather than asserting a
    co-billing relationship.
    """
    credits = [
        (credit, _credit_name(credit))
        for credit in artist_credits or ()
        if isinstance(credit, dict) and _credit_name(credit)
    ]
    if not credits:
        return "", ()

    primary = credits[0][1]
    relation_features = _feature_relation_names(relations)
    has_relation_features = bool(
        relation_features & {normalize_text(name) for _credit, name in credits[1:]}
    )
    features: list[str] = []

    def add_feature(name: str) -> None:
        if normalize_text(name) not in {normalize_text(existing) for existing in features}:
            features.append(name)

    for index, (_credit, name) in enumerate(credits[1:], start=1):
        previous_joinphrase = str(credits[index - 1][0].get("joinphrase") or "")
        normalized_name = normalize_text(name)
        if normalized_name in relation_features or _FEATURE_JOIN_RE.search(previous_joinphrase):
            add_feature(name)
            continue
        if not previous_joinphrase.strip():
            if not has_relation_features:
                add_feature(name)
            continue
        if _CO_ARTIST_JOIN_RE.fullmatch(previous_joinphrase):
            primary = f"{primary}{previous_joinphrase}{name}"
            continue
        # Preserve any explicit non-feature joinphrase rather than replacing
        # it with a made-up relationship.
        primary = f"{primary}{previous_joinphrase}{name}"

    return primary.strip(), tuple(features)


def _format_artist_identity(
    primary: str,
    features: tuple[str, ...],
) -> str:
    if not features:
        return primary
    return f"{primary} feat. {', '.join(features)}"


def _artist_credit_name(
    artist_credits: list | None,
    relations: list | None = None,
) -> str:
    """Return a readable credit without inventing blank joinphrases."""
    primary, features = _artist_credit_identity(artist_credits, relations)
    return _format_artist_identity(primary, features)


def _merge_feature_names(*groups: tuple[str, ...]) -> tuple[str, ...]:
    def equivalent(left: str, right: str) -> bool:
        left_normalized = normalize_text(left)
        right_normalized = normalize_text(right)
        left_squashed = re.sub(r"\W+", "", left_normalized)
        right_squashed = re.sub(r"\W+", "", right_normalized)
        if left_squashed == right_squashed:
            return True
        shorter = min(left_squashed, right_squashed, key=len)
        if len(shorter) < 4:
            return False
        return (
            SequenceMatcher(
                None,
                left_squashed,
                right_squashed,
            ).ratio()
            >= 0.86
        )

    merged: list[str] = []
    for group in groups:
        for feature in group:
            normalized = normalize_text(feature)
            if not normalized:
                continue
            if any(
                equivalent(feature, existing)
                or normalized in normalize_text(existing)
                or normalize_text(existing) in normalized
                for existing in merged
            ):
                continue
            merged.append(feature)
    return tuple(merged)


def _title_with_features(
    title: str,
    features: tuple[str, ...],
) -> str:
    clean_title, existing_features = split_features(str(title or ""))
    merged = _merge_feature_names(existing_features, features)
    if not merged:
        return clean_title
    return f"{clean_title} (feat. {', '.join(merged)})"


def _values(items: list | None, key: str = "name") -> list[str]:
    result = []
    for item in items or []:
        if isinstance(item, str) and item:
            result.append(item)
        elif isinstance(item, dict) and item.get(key):
            result.append(item[key])
    return result


def _release_type(release: dict) -> str:
    group = release.get("release-group") or {}
    return str(group.get("primary-type") or "").casefold()


def _candidate_score(
    release: dict,
    local_evidence: dict[str, object],
) -> ReleaseCandidate:
    reasons: list[str] = []
    score = 0
    status = str(release.get("status") or "")
    release_type = _release_type(release)
    album = str(local_evidence.get("album") or "")
    album_artist = str(local_evidence.get("album_artist") or "")
    release_artist = _artist_credit_name(release.get("artist-credit"))
    text_representation = release.get("text-representation") or {}
    language = str(text_representation.get("language") or "")
    script = str(text_representation.get("script") or "")

    if status.casefold() == "official":
        score += 20
        reasons.append("official release")
    elif status:
        score -= 35
    if release_type in {"album", "single", "ep"}:
        score += 10
    elif release_type in {"compilation", "broadcast", "other"}:
        score -= 30
    if album and normalize_text(album) == normalize_text(str(release.get("title", ""))):
        score += 100
        reasons.append("matching album tag")
    if (
        album_artist
        and release_artist
        and normalize_text(album_artist) == normalize_text(release_artist)
    ):
        score += 35
        reasons.append("matching album artist")
    if language.casefold() in {"en", "eng"}:
        score += 8
        reasons.append("English release metadata")
    if script.casefold() in {"latn", "latin"}:
        score += 8
        reasons.append("Latin-script release metadata")
    if local_evidence.get("musicbrainz_albumid") == release.get("id"):
        score += 1000
        reasons.append("matching embedded release ID")

    return ReleaseCandidate(
        release_id=str(release.get("id") or ""),
        title=str(release.get("title") or ""),
        date=str(release.get("date") or ""),
        status=status,
        country=str(release.get("country") or ""),
        language=language,
        script=script,
        release_group_type=release_type,
        score=score,
        reasons=tuple(reasons),
    )


def _eligible_canonical_releases(
    candidates: list[ReleaseCandidate],
) -> list[ReleaseCandidate]:
    return [
        candidate
        for candidate in candidates
        if candidate.status.casefold() == "official"
        and candidate.release_group_type not in {"compilation", "broadcast", "other"}
    ]


def _earliest_release(candidates: list[ReleaseCandidate]) -> ReleaseCandidate:
    return min(
        candidates,
        key=lambda candidate: (
            candidate.date or "9999-99-99",
            candidate.release_id,
        ),
    )


def _canonical_release_selection(
    candidates: list[ReleaseCandidate],
) -> tuple[ReleaseCandidate | None, tuple[str, ...]]:
    eligible = _eligible_canonical_releases(candidates)
    if eligible:
        latin_metadata = [
            candidate
            for candidate in eligible
            if candidate.language.casefold() in {"en", "eng"}
            or candidate.script.casefold() in {"latn", "latin"}
        ]
        selected = _earliest_release(latin_metadata or eligible)
        return selected, (
            "No local release evidence; selected the earliest official non-compilation release.",
        )
    return None, ("No suitable official canonical release; release-specific metadata was skipped.",)


def _has_release_evidence(evidence: dict[str, object]) -> bool:
    return any(
        evidence.get(key)
        for key in (
            "album",
            "album_artist",
            "musicbrainz_albumid",
            "tracknumber",
            "discnumber",
        )
    )


def _release_ordering(candidate: ReleaseCandidate) -> tuple[int, int, str, str]:
    official = int(candidate.status.casefold() == "official")
    standard_type = int(candidate.release_group_type in {"album", "single", "ep"})
    return (
        candidate.score,
        official + standard_type,
        "".join("9" if character.isdigit() else character for character in candidate.date)
        or "9999-99-99",
        candidate.release_id,
    )


def _ranked_release_selection(
    candidates: list[ReleaseCandidate],
) -> tuple[ReleaseCandidate | None, tuple[str, ...]]:
    ranked = sorted(candidates, key=_release_ordering, reverse=True)
    best = ranked[0]
    warnings: list[str] = []
    tied = [candidate for candidate in ranked if candidate.score == best.score]
    if len(tied) > 1 and best.score > 0:
        warnings.append("Multiple releases match local evidence; selected the earliest.")
        best = _earliest_release(tied)
    elif best.score <= 0:
        return _canonical_release_selection(candidates)
    return best, tuple(warnings)


def select_release(
    releases: list[dict],
    local_evidence: dict[str, object] | None = None,
) -> tuple[ReleaseCandidate | None, tuple[str, ...]]:
    """Choose a canonical release conservatively from recording appearances."""
    evidence = local_evidence or {}
    candidates = [_candidate_score(release, evidence) for release in releases if release.get("id")]
    if not candidates:
        return None, ("MusicBrainz returned no release candidates.",)
    if not _has_release_evidence(evidence):
        return _canonical_release_selection(candidates)
    return _ranked_release_selection(candidates)


def _cached_musicbrainz(
    namespace: str,
    cache_key: str,
    request,
) -> dict | None:
    cache = enrichment_cache()
    if (cached := cache.get(namespace, cache_key)) is not None:
        return cached
    key = namespace, cache_key
    with _CACHE_REQUEST_LOCK:
        request_lock = _IN_FLIGHT_REQUESTS.setdefault(key, threading.Lock())
    try:
        with request_lock:
            if (cached := cache.get(namespace, cache_key)) is not None:
                return cached
            _rate_limit()
            response = request()
            cache.set(namespace, cache_key, response)
            return response
    finally:
        with _CACHE_REQUEST_LOCK:
            if _IN_FLIGHT_REQUESTS.get(key) is request_lock:
                del _IN_FLIGHT_REQUESTS[key]


def _release_track(release: dict, recording_id: str) -> tuple[dict, dict] | None:
    for medium in release.get("medium-list", []):
        for track in medium.get("track-list", []):
            recording = track.get("recording") or {}
            if recording.get("id") == recording_id:
                return medium, track
    return None


def _relation_credits(recording: dict) -> dict[str, list[str]]:
    role_map = {
        "composer": "composer",
        "writer": "writer",
        "lyricist": "lyricist",
        "producer": "producer",
        "remixer": "remixer",
        "mix": "mixer",
        "performer": "performer",
    }
    role_credits: dict[str, list[str]] = {}
    for relation in recording.get("artist-relation-list", []):
        if not isinstance(relation, dict):
            continue
        role = role_map.get(str(relation.get("type") or "").casefold())
        artist = relation.get("artist") or {}
        name = artist.get("name") if isinstance(artist, dict) else ""
        if role and name:
            role_credits.setdefault(role, []).append(name)
    return role_credits


def _work_credits(recording: dict, mb) -> dict[str, list[str]]:
    """Fetch composer/writer roles when a recording points at one work."""
    work_id = next(
        (
            (relation.get("work") or {}).get("id")
            for relation in recording.get("work-relation-list", [])
            if isinstance(relation, dict) and isinstance(relation.get("work"), dict)
        ),
        "",
    )
    if not work_id:
        return {}
    payload = _cached_musicbrainz(
        "musicbrainz-work",
        work_id,
        lambda: mb.get_work_by_id(work_id, includes=["artist-rels"]),
    )
    if not payload:
        return {}
    work = payload.get("work") or {}
    return _relation_credits({"artist-relation-list": work.get("artist-relation-list", [])})


def _metadata_from_release(
    recording: dict,
    release: dict,
    recording_id: str,
) -> dict[str, object]:
    recording_title = str(recording.get("title") or "")
    artist, features = _artist_credit_identity(
        recording.get("artist-credit"),
        recording.get("artist-relation-list"),
    )
    values: dict[str, object] = {
        "artist": artist,
        "title": _title_with_features(recording_title, features),
        "musicbrainz_recordingid": recording_id,
        "isrc": _values(recording.get("isrc-list"), "id"),
        "genre": _values(recording.get("genre-list")),
        "tag": _values(recording.get("tag-list")),
    }
    values.update(_relation_credits(recording))
    if not release:
        return {key: value for key, value in values.items() if value}

    release_group = release.get("release-group") or {}
    values.update(
        {
            "album": release.get("title", ""),
            "album_artist": _artist_credit_name(release.get("artist-credit")),
            "date": release.get("date", ""),
            "release_country": release.get("country", ""),
            "release_status": release.get("status", ""),
            "release_type": release_group.get("primary-type", ""),
            "language": release.get("text-representation", {}).get("language", ""),
            "script": release.get("text-representation", {}).get("script", ""),
            "musicbrainz_albumid": release.get("id", ""),
            "musicbrainz_releasegroupid": release_group.get("id", ""),
            "barcode": release.get("barcode", ""),
        }
    )
    label_info = release.get("label-info-list") or []
    if label_info:
        first = label_info[0]
        label = first.get("label") or {}
        values["label"] = label.get("name", "") if isinstance(label, dict) else ""
        values["catalog_number"] = first.get("catalog-number", "")
    matched = _release_track(release, recording_id)
    if matched is not None:
        medium, track = matched
        track_artist = _artist_credit_name(track.get("artist-credit"))
        if track_artist:
            values["artist"] = prefer_latin_text(artist, track_artist)
        track_title = str(track.get("title") or "")
        if track_title:
            values["title"] = _title_with_features(
                prefer_latin_text(recording_title, track_title),
                features,
            )
        values.update(
            {
                # MusicBrainz's JSON API returns these as ints; on-disk tags
                # are always plain text, so leaving them as ints here would
                # make every file with a disc/track count look "changed"
                # against its own already-correct value on every re-run.
                "tracknumber": str(track.get("number") or track.get("position") or ""),
                "tracktotal": str(medium.get("track-count") or ""),
                "discnumber": str(medium.get("position") or ""),
                "disctotal": str(release.get("medium-count") or ""),
            }
        )
    return {key: value for key, value in values.items() if value not in ("", [], None)}


def _recording_details(recording_id: str, mb) -> dict | None:
    payload = _cached_musicbrainz(
        "musicbrainz-recording",
        recording_id,
        lambda: mb.get_recording_by_id(
            recording_id,
            includes=[
                "artists",
                "releases",
                "isrcs",
                "tags",
                "artist-rels",
                "work-rels",
            ],
        ),
    )
    if not payload or not payload.get("recording"):
        return None
    return payload["recording"]


def _release_details(release: ReleaseCandidate | None, mb) -> dict:
    if release is None:
        return {}
    payload = _cached_musicbrainz(
        "musicbrainz-release",
        release.release_id,
        lambda: mb.get_release_by_id(
            release.release_id,
            includes=[
                "artists",
                "recordings",
                "labels",
                "release-groups",
            ],
        ),
    )
    return (payload or {}).get("release") or {}


def _enrichment_values(
    recording: dict,
    detailed_release: dict,
    recording_id: str,
    mb,
) -> dict[str, object]:
    values = _metadata_from_release(recording, detailed_release, recording_id)
    for role, names in _work_credits(recording, mb).items():
        values.setdefault(role, names)
    return values


def enrich_recording(
    recording_id: str,
    *,
    local_evidence: dict[str, object] | None = None,
) -> EnrichmentResult | None:
    """Fetch a recording, select a corroborated release, and return rich fields."""
    if not recording_id or not _available():
        return None
    mb = _mb()
    recording = _recording_details(recording_id, mb)
    if recording is None:
        return None
    release, warnings = select_release(
        recording.get("release-list") or [],
        local_evidence,
    )
    detailed_release = _release_details(release, mb)
    values = _enrichment_values(recording, detailed_release, recording_id, mb)
    confidence = "high" if release and not warnings else "medium"
    return EnrichmentResult(
        recording_id=recording_id,
        values=values,
        release_id=release.release_id if release else "",
        release_group_id=str((detailed_release.get("release-group") or {}).get("id") or ""),
        confidence=confidence,
        warnings=warnings,
        provenance=(
            "MusicBrainz recording",
            *("MusicBrainz release" for _ in [release] if release is not None),
        ),
    )


def enrich_track(track: TrackInfo) -> TrackInfo:
    """Fill a track's missing identity fields from MusicBrainz when possible."""
    if track.strategy == "musicbrainz":
        result = lookup_track_by_album(
            track.mb_album,
            track.mb_track_num,
            artist_hint=track.artist,
        )
        if result:
            return replace(
                track,
                artist=result["artist"],
                title=result["title"],
                needs_lookup=False,
            )
        return replace(
            track,
            skip_reason=(
                f'MusicBrainz: no match for "{track.mb_album}" track {track.mb_track_num}'
            ),
        )
    if track.strategy in {"ocremix_old_tags", "ocremix_filename"} and not track.remixers:
        remixers = lookup_ocremix_remixers(track.game, track.title)
        if remixers:
            return replace(
                track,
                remixers=tuple(remixers),
                needs_lookup=False,
            )
    return track
