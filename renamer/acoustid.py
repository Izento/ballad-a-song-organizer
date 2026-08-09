"""Optional AcoustID lookup and local result caching."""

import json
import math
import os
import threading
from contextlib import contextmanager, suppress
from dataclasses import dataclass

from .filename_parser import normalize_text
from .fingerprint import fingerprint_file_details
from .online import Provider, ProviderError, RateLimiter, RequestPolicy
from .runtime import app_paths, atomic_write_json, resolve_fpcalc
from .track_identity import filename_identity_hint, has_non_latin_text

_CACHE_PATH = str(app_paths()["cache"] / "acoustid_cache.json")
_CACHE_LOCK = threading.RLock()
_REQUEST_POLICY = RequestPolicy(
    provider=Provider.ACOUSTID,
    limiter=RateLimiter(1 / 3),
)

MIN_CONFIDENCE = 0.70
_CACHE_VERSION = 4


@dataclass
class _CacheState:
    entries: dict | None = None
    batch_depth: int = 0


_CACHE_STATE = _CacheState()


def _load_cache() -> dict:
    with _CACHE_LOCK:
        entries = _CACHE_STATE.entries
        if entries is None:
            try:
                with open(_CACHE_PATH, encoding="utf-8") as fh:
                    entries = json.load(fh)
            except (FileNotFoundError, json.JSONDecodeError):
                entries = {}
            _CACHE_STATE.entries = entries
        return entries


def _save_cache() -> None:
    with _CACHE_LOCK:
        if _CACHE_STATE.entries is None:
            return
        snapshot = dict(_CACHE_STATE.entries)
    with suppress(OSError):
        atomic_write_json(app_paths()["cache"] / "acoustid_cache.json", snapshot)


@contextmanager
def cache_write_batch():
    """Persist AcoustID cache once after a concurrent batch completes."""
    with _CACHE_LOCK:
        _CACHE_STATE.batch_depth += 1
    try:
        yield
    finally:
        with _CACHE_LOCK:
            _CACHE_STATE.batch_depth -= 1
            flush = _CACHE_STATE.batch_depth == 0
        if flush:
            _save_cache()


def _cache_should_flush() -> bool:
    with _CACHE_LOCK:
        return _CACHE_STATE.batch_depth == 0


def _file_key(path: str) -> str:
    """Stable cache key: path + mtime + size. Invalidates if file is modified."""
    try:
        s = os.stat(path)
        return f"v{_CACHE_VERSION}|{os.path.abspath(path)}|{s.st_mtime_ns}|{s.st_size}|{s.st_ino}"
    except OSError:
        return f"v{_CACHE_VERSION}|{path}"


def _identity_similarity(
    filename_hint: tuple[str, str],
    artist: str,
    title: str,
) -> int:
    hint_artist, hint_title = (normalize_text(value) for value in filename_hint)
    candidate_artist = normalize_text(artist)
    candidate_title = normalize_text(title)
    score = 0
    if hint_artist and hint_artist == candidate_artist:
        score += 1
    if hint_title == candidate_title:
        score += 3
    elif hint_title and (hint_title in candidate_title or candidate_title in hint_title):
        score += 2
    return score


def _source_count(recording: dict) -> int:
    try:
        return int(recording.get("sources", 0))
    except (TypeError, ValueError):
        return 0


def _recording_artist(recording: dict) -> str | None:
    artists = recording.get("artists") or ()
    if not artists:
        return None
    return "".join(artist.get("name", "") + artist.get("joinphrase", "") for artist in artists)


def _recording_rank(
    recording: dict,
    filename_hint: tuple[str, str],
) -> tuple[int, int, int, int]:
    artist = _recording_artist(recording) or ""
    title = str(recording.get("title") or "")
    identity_score = _identity_similarity(filename_hint, artist, title)
    latin_score = int(not has_non_latin_text(artist)) + int(not has_non_latin_text(title))
    exact_title = int(
        bool(filename_hint[1]) and normalize_text(title) == normalize_text(filename_hint[1])
    )
    return identity_score, latin_score, exact_title, _source_count(recording)


def _select_recording(response: dict, path: str) -> tuple[float, dict] | None:
    """Choose a supported recording from the strongest acoustic match.

    An AcoustID result identifies an audio fingerprint; it can be linked to
    several MusicBrainz recordings. The match score applies to that fingerprint,
    not to any one linked recording. A matching filename title resolves a
    conflicting group; otherwise source consensus chooses the recording.
    """
    if response.get("status") != "ok":
        return None
    scored_results = []
    for result in response.get("results", ()):
        try:
            score = float(result["score"])
        except (KeyError, TypeError, ValueError):
            continue
        scored_results.append((score, result))
    scored_results.sort(key=lambda value: value[0], reverse=True)

    filename_hint = filename_identity_hint(path) or ("", "")
    while scored_results:
        score = scored_results[0][0]
        if score < MIN_CONFIDENCE:
            break
        top_results = [
            result
            for candidate_score, result in scored_results
            if math.isclose(candidate_score, score, rel_tol=0, abs_tol=1e-6)
        ]
        scored_results = [
            (candidate_score, result)
            for candidate_score, result in scored_results
            if not math.isclose(candidate_score, score, rel_tol=0, abs_tol=1e-6)
        ]
        recordings = [
            (
                _recording_rank(recording, filename_hint),
                recording,
            )
            for result in top_results
            for recording in result.get("recordings", ())
            if recording.get("title") and _recording_artist(recording)
        ]
        if recordings:
            exact_title_matches = [recording for recording in recordings if recording[0][2]]
            choices = exact_title_matches or recordings
            _, recording = max(choices, key=lambda value: value[0])
            return score, recording
    return None


def _cached_lookup(cache: dict, key: str) -> tuple[bool, dict | None]:
    with _CACHE_LOCK:
        if key in cache:
            return True, cache[key]
    return False, None


def _lookup_result(
    acoustid,
    path: str,
    api_key: str,
) -> tuple[bool, dict | None]:
    fingerprint, duration, fingerprint_error = fingerprint_file_details(path)
    if fingerprint_error or not fingerprint or duration is None:
        return False, None

    response = _REQUEST_POLICY.request(
        lambda: acoustid.lookup(
            api_key,
            fingerprint,
            duration,
            meta=["recordings", "sources"],
        ),
        transient_errors=(
            acoustid.WebServiceError,
            acoustid.NoBackendError,
            OSError,
        ),
    )
    if response.get("status") != "ok":
        raise acoustid.WebServiceError(f"AcoustID response status: {response.get('status')}")
    selected = _select_recording(response, path)
    if selected is None:
        return True, None
    score, recording = selected
    result = _parse_result(
        _recording_artist(recording) or "",
        recording["title"],
        score,
        recording_id=recording.get("id"),
    )
    result["sources"] = _source_count(recording)
    return True, result


def lookup(path: str, api_key: str) -> dict | None:
    """
    Fingerprint an audio file and query AcoustID.

    Returns dict(artist, title, feat_artists, score) on a confident match,
    or None if no match found, fpcalc is unavailable, or confidence is low.

    Results are persisted to acoustid_cache.json so re-running on unchanged
    files is instant.
    """
    cache = _load_cache()
    key = _file_key(path)
    has_cached_result, cached_result = _cached_lookup(cache, key)
    if has_cached_result:
        return cached_result
    try:
        import acoustid
    except ImportError as exc:
        raise RuntimeError("pyacoustid is not installed. Run: uv pip install pyacoustid") from exc
    if not resolve_fpcalc():
        return None
    try:
        should_cache, result = _lookup_result(acoustid, path, api_key)
    except (
        acoustid.FingerprintGenerationError,
        acoustid.WebServiceError,
        acoustid.NoBackendError,
        ProviderError,
        OSError,
    ):
        # fpcalc missing/broken, API error, or file unreadable.
        # Don't cache transient errors — allow a retry next run.
        return None
    if not should_cache:
        return None
    with _CACHE_LOCK:
        cache[key] = result
    if _cache_should_flush():
        _save_cache()
    return result


def _parse_result(
    artist: str,
    title: str,
    score: float,
    recording_id: str | None = None,
) -> dict:
    """
    Normalize AcoustID result into our structured format.
    Splits feat. artists out of both the title and the artist string.
    """
    from .filename_builder import split_feat

    clean_title, feat_from_title = split_feat(title)
    clean_artist, feat_from_artist = split_feat(artist)

    # Deduplicate across both sources
    seen = {a.lower() for a in feat_from_artist}
    feat_artists = feat_from_artist + [f for f in feat_from_title if f.lower() not in seen]

    result = {
        "artist": clean_artist.strip(),
        "title": clean_title.strip(),
        "feat_artists": feat_artists,
        "score": round(score, 3),
    }
    if recording_id:
        result["recording_id"] = recording_id
    return result
