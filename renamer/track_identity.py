"""Single filename identity and online-version reconciliation policy."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .filename_parser import RegularName, normalize_text, parse_regular_filename
from .qualifiers import (
    has_explicit_variant,
    has_instrumental_qualifier,
    has_matching_qualifier,
    parse_qualifiers,
    preserve_local_versions,
)

_VERSION_BLOCK_RE = re.compile(r"[\(\[]\s*[^\)\]]+?\s*[\)\]]")
_VERSION_CREDIT_RE = re.compile(r"\b(?:edit|mix|remix|version)\b", re.IGNORECASE)
_IDENTITY_TOKEN_RE = re.compile(r"[^\W_]+")
# Tokens too common to prove two identities are related on their own.
_GENERIC_IDENTITY_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "dj",
        "feat",
        "featuring",
        "ft",
        "mc",
        "mr",
        "of",
        "the",
        "vs",
        "x",
    }
)
_PLACEHOLDER_ARTISTS = frozenset(
    {"no artist", "unknown", "unknown artist", "various artists", "va"}
)


@dataclass(frozen=True)
class TrackIdentity:
    artist: str
    title: str
    contributors: tuple[str, ...] = ()
    qualifiers: tuple[str, ...] = ()
    version: str = ""
    source: str = "filename"

    @property
    def normalized_artist(self) -> str:
        return normalize_text(self.artist)

    @property
    def normalized_title(self) -> str:
        return normalize_text(self.title)

    @property
    def normalized_core_title(self) -> str:
        return normalize_text(_VERSION_BLOCK_RE.sub(" ", self.title))

    @property
    def key(self) -> tuple:
        return (
            self.normalized_artist,
            self.normalized_title,
            tuple(
                sorted(normalize_text(value) for value in self.contributors)
            ),
            tuple(
                sorted(normalize_text(value) for value in self.qualifiers)
            ),
            normalize_text(self.version),
        )

    @property
    def core_key(self) -> tuple[str, str]:
        return self.normalized_artist, self.normalized_core_title

    @classmethod
    def from_regular(cls, name: RegularName) -> TrackIdentity:
        return cls(
            artist=name.artist,
            title=name.title,
            contributors=name.features,
            qualifiers=name.qualifiers,
            version=", ".join(name.qualifiers),
        )


@dataclass(frozen=True)
class VersionResolution:
    title: str
    exact_recording_id: str = ""
    derived_from_recording_id: str = ""
    warning: str = ""

    @property
    def is_derivative(self) -> bool:
        return bool(self.derived_from_recording_id and not self.exact_recording_id)


def parse_filename_identity(path: str) -> TrackIdentity | None:
    parsed = parse_regular_filename(Path(path).name)
    return TrackIdentity.from_regular(parsed) if parsed is not None else None


def filename_identity_hint(path: str) -> tuple[str, str] | None:
    """Return conservative artist/title evidence from a filename."""
    identity = parse_filename_identity(path)
    if identity is not None:
        return identity.artist, identity.title
    stem = Path(path).stem.replace("_", " ")
    if " - " not in stem:
        return None
    artist, title = (part.strip() for part in stem.split(" - ", 1))
    artist_key = normalize_text(artist)
    if (
        not artist
        or not title
        or artist_key in {"track", "unknown artist", "various artists", "va"}
        or artist_key.isdigit()
    ):
        return None
    return artist, title


def _fold(value: str) -> str:
    """Normalize for comparison, treating "Tiesto" and "Tiësto" as one name."""
    decomposed = unicodedata.normalize("NFKD", normalize_text(value))
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


def _identity_tokens(value: str) -> set[str]:
    return set(_IDENTITY_TOKEN_RE.findall(_fold(value)))


def _distinctive_tokens(value: str) -> set[str]:
    tokens = _identity_tokens(value)
    return (tokens - _GENERIC_IDENTITY_TOKENS) or tokens


def _squashed(value: str) -> str:
    return "".join(_IDENTITY_TOKEN_RE.findall(_fold(value)))


def _survives_in(evidence: str, proposal: str) -> bool:
    tokens = _distinctive_tokens(evidence)
    if not tokens:
        return True
    if tokens & _identity_tokens(proposal):
        return True
    # "Bass Hunter" and "Basshunter" share no whole token, so compare the
    # separator-free forms too before calling an identity unrecognizable.
    squashed = _squashed(evidence)
    return bool(squashed) and squashed in _squashed(proposal)


def _substantially_survives_in(evidence: str, proposal: str) -> bool:
    tokens = _distinctive_tokens(evidence)
    if not tokens:
        return True
    overlap = tokens & _identity_tokens(proposal)
    if len(overlap) * 2 >= len(tokens):
        return True
    squashed = _squashed(evidence)
    return bool(squashed) and squashed in _squashed(proposal)


def identity_is_recognizable(
    *,
    local_artist: str,
    local_title: str,
    proposed_artist: str,
    proposed_title: str,
) -> bool:
    """Whether a proposed identity still resembles the file it came from.

    A wrong recording match -- a bad embedded MusicBrainz ID, or a
    fingerprint collision -- renames a song into an unrelated one while
    every provider signal downstream looks perfectly healthy. The one check
    no provider can do for us is whether its answer has anything to do with
    the file we asked about.

    The artist carries the check. A remix credited to its remixer
    legitimately rewrites both fields ("Beatman & Ludmilla - Bazantar" ->
    "Paul Oakenfold - Ready Steady Go! (Beatman & Ludmilla radio edit)"),
    and the local artist still shows up somewhere in the result. A title
    that survives while the artist vanishes entirely is instead the shape of
    a same-title-different-song mixup ("Alex Sayz - Faces" -> "Say Just
    Words - Faces"). With no local artist to check, the title is all the
    evidence there is.
    """
    if _distinctive_tokens(local_artist):
        if _survives_in(local_artist, proposed_artist):
            return True
        if _survives_in(local_artist, proposed_title):
            return _substantially_survives_in(local_title, proposed_title) or bool(
                _VERSION_CREDIT_RE.search(proposed_title)
            )
        return False
    return _survives_in(local_title, proposed_title)


def is_placeholder_artist(value: str) -> bool:
    """Whether an artist value is a non-identity placeholder."""
    return normalize_text(value) in _PLACEHOLDER_ARTISTS


def artist_appears_in(text: str, artist: str) -> bool:
    """Whether an artist name shows up anywhere in free-form local text.

    Filenames often put a label or channel where the parser expects the
    artist ("Future House Records - SvanteG & Abedz - Tantrum"), or credit
    an alias the tags don't ("Skrillex & Diplo" for "Jack U"). The matched
    artist is still named in the file, just not in the field that was
    parsed out of it, and that's enough to consider the match plausible.
    """
    return _survives_in(artist, text)


def reconcile_online_version(
    path: str,
    online_title: str,
    recording_id: str = "",
) -> VersionResolution:
    """Preserve explicit local versions and classify source-only matches."""
    identity = parse_filename_identity(path)
    if identity is None:
        return VersionResolution(
            title=online_title,
            exact_recording_id=recording_id,
        )
    local_title = identity.title
    local_has_variant = (
        has_explicit_variant(local_title)
        or has_instrumental_qualifier(local_title)
    )
    qualifiers_match = has_matching_qualifier(local_title, online_title)
    derivative = bool(recording_id and local_has_variant and not qualifiers_match)
    online_has_variant = any(
        qualifier.is_recording_identity
        for qualifier in parse_qualifiers(online_title)
    )
    warning = ""
    if local_has_variant and online_has_variant and not qualifiers_match:
        warning = (
            "Version qualifier conflicts with AcoustID metadata; "
            "review the proposed filename."
        )
    elif derivative:
        warning = (
            "Local version label is not present in AcoustID metadata; "
            "treated as a possible local derivative."
        )
    return VersionResolution(
        title=preserve_local_versions(local_title, online_title),
        exact_recording_id="" if derivative else recording_id,
        derived_from_recording_id=recording_id if derivative else "",
        warning=warning,
    )


__all__ = [
    "VersionResolution",
    "TrackIdentity",
    "artist_appears_in",
    "filename_identity_hint",
    "identity_is_recognizable",
    "is_placeholder_artist",
    "parse_filename_identity",
    "reconcile_online_version",
]
