"""Build safe, canonical filenames from extracted track metadata."""

import re
from collections.abc import Iterable

from .filename_parser import normalize_text, normalize_title_text

WINDOWS_UNSAFE_RE = re.compile(r'[\x00-\x1f<>"/\\|?*]')
SUBTITLE_COLON_RE = re.compile(r":\s+")  # "Game: Subtitle" → "Game - Subtitle"
WINDOWS_RESERVED_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)

# Strips trailing wrapped and bare OC ReMix labels from a title.  A few older
# filenames contain both forms, so the helper below applies this repeatedly.
OCREMIX_SUFFIX_RE = re.compile(
    r"(?:\s*[\(\[]\s*OC[\s_]*Re[Mm]ix\s*[\)\]]|"
    r"\s+\bOC[\s_]*Re[Mm]ix\b|_OC[\s_]*Re[Mm]ix)\s*$",
    re.IGNORECASE,
)
_OCREMIX_CREDIT_SUFFIX_RE = re.compile(
    r"\s*[\(\[](?P<credits>[^()\[\]]+)[\)\]]\s*$",
)


def _normalized_remixers(values: Iterable[str]) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = normalize_title_text(str(value))
        key = normalize_text(name)
        if name and key not in seen:
            names.append(name)
            seen.add(key)
    return tuple(names)


def safe_part(s: str) -> str:
    """
    Make a filename component safe for Windows.
    - 'Game: Subtitle' → 'Game - Subtitle'  (colon-space = subtitle separator)
    - Remaining colons → '_'
    - ?, !, and other unsafe chars → stripped
    """
    s = SUBTITLE_COLON_RE.sub(" - ", s)  # "Title: Subtitle" → "Title - Subtitle"
    s = s.replace(":", "_")  # lone colons get underscored
    s = WINDOWS_UNSAFE_RE.sub("_", s)
    s = s.strip(". ")
    if not s:
        return "_"
    if WINDOWS_RESERVED_RE.match(s):
        s = f"_{s}"
    # Keep enough room for the extension and a parent directory on Windows.
    return s[:240].rstrip(". ")


def split_feat(raw: str) -> tuple[str, list[str]]:
    """
    Extract featuring artists from a text field.

    Returns (cleaned_text, [feat_artist, ...]).
    Works on both artist fields ("Artist feat. X, Y") and
    title fields ("Song Title (feat. X and Y)").
    """
    # Keep one conservative feature parser for both filename parsing and tag
    # extraction.  In particular, this leaves labels such as "(Remix)" and
    # "(Radio Edit)" in the title.
    from .filename_parser import split_features

    cleaned, features = split_features(raw)
    return cleaned, list(features)


def strip_ocremix_suffix(title: str) -> str:
    cleaned = title or ""
    while True:
        stripped = OCREMIX_SUFFIX_RE.sub("", cleaned).strip()
        if stripped == cleaned:
            return stripped
        cleaned = stripped


def strip_ocremix_remixer(title: str, remixers: Iterable[str]) -> str:
    clean = strip_ocremix_suffix(normalize_title_text(title))
    names = _normalized_remixers(remixers)
    match = _OCREMIX_CREDIT_SUFFIX_RE.search(clean)
    if not names or match is None:
        return clean
    existing = _normalized_remixers(match.group("credits").split(","))
    if tuple(normalize_text(name) for name in existing) == tuple(
        normalize_text(name) for name in names
    ):
        return clean[: match.start()].rstrip()
    return clean


def format_ocremix_title(title: str, remixers: Iterable[str]) -> str:
    names = _normalized_remixers(remixers)
    clean = strip_ocremix_remixer(title, names)
    return f"{clean} ({', '.join(names)})" if names else clean


def build_filename(track) -> str:
    """Construct the target filename from a TrackInfo object."""
    ext = (track.ext or ".mp3").lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    ext = f".{ext.rsplit('.', 1)[-1]}"

    if track.is_ocremix:
        return _ocremix_name(track) + ext
    return _regular_name(track) + ext


def _ocremix_name(track) -> str:
    game = safe_part(track.game) if track.game else "Unknown Game"
    title = (
        safe_part(strip_ocremix_remixer(track.title, track.remixers))
        if track.title
        else "Unknown Title"
    )

    remixers = [safe_part(name) for name in _normalized_remixers(track.remixers)]
    if remixers:
        remixer_str = ", ".join(remixers)
        return f"{game} - {title} ({remixer_str}) [OC ReMix]"

    return f"{game} - {title} [OC ReMix]"


def _regular_name(track) -> str:
    artist = safe_part(track.artist) if track.artist else "Unknown Artist"
    title = safe_part(normalize_title_text(track.title)) if track.title else "Unknown Title"

    if track.feat_artists:
        features = []
        seen = set()
        for feature in track.feat_artists:
            cleaned = normalize_title_text(feature)
            key = cleaned.casefold()
            if cleaned and key not in seen:
                features.append(safe_part(cleaned))
                seen.add(key)
        feat_str = ", ".join(features)
        if not feat_str:
            return f"{artist} - {title}"
        return f"{artist} - {title} (feat. {feat_str})"

    return f"{artist} - {title}"
