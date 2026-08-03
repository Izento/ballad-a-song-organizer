"""Single canonical field registry for every supported media container."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.metadata import CanonicalMetadata


@dataclass(frozen=True)
class FieldSpec:
    canonical: str
    id3: str = ""
    vorbis: str = ""
    mp4: str = ""
    asf: str = ""
    multi: bool = False


FIELDS = (
    FieldSpec("artist", "TPE1", "artist", "\xa9ART", "Author"),
    FieldSpec("title", "TIT2", "title", "\xa9nam", "Title"),
    FieldSpec("album", "TALB", "album", "\xa9alb", "WM/AlbumTitle"),
    FieldSpec("album_artist", "TPE2", "albumartist", "aART", "WM/AlbumArtist"),
    FieldSpec("grouping", "TIT1", "grouping", "\xa9grp", "WM/ContentGroupDescription"),
    FieldSpec("subtitle", "TIT3", "subtitle", "", "WM/SubTitle"),
    FieldSpec("date", "TDRC", "date", "\xa9day", "WM/Year"),
    FieldSpec("genre", "TCON", "genre", "\xa9gen", "WM/Genre", multi=True),
    FieldSpec("composer", "TCOM", "composer", "\xa9wrt", "WM/Composer", multi=True),
    FieldSpec("writer", "", "writer", "", "WM/Writer", multi=True),
    FieldSpec("lyricist", "TEXT", "lyricist", "", "WM/Lyrics", multi=True),
    FieldSpec("producer", "", "producer", "", "WM/Producer", multi=True),
    FieldSpec("remixer", "TPE4", "remixer", "", "WM/Remixer", multi=True),
    FieldSpec("mixer", "", "mixer", "", "WM/Mixer", multi=True),
    FieldSpec("performer", "TPE3", "performer", "", "WM/Artist", multi=True),
    FieldSpec("label", "TPUB", "label", "", "WM/Publisher"),
    FieldSpec("catalog_number", "", "catalognumber", "", "WM/CatalogNo"),
    FieldSpec("barcode", "", "barcode", "", "WM/Barcode"),
    FieldSpec("isrc", "TSRC", "isrc", "", "WM/ISRC", multi=True),
    FieldSpec(
        "musicbrainz_recordingid",
        "",
        "musicbrainz_recordingid",
        "",
        "MusicBrainz Recording Id",
    ),
    FieldSpec(
        "musicbrainz_albumid",
        "",
        "musicbrainz_albumid",
        "",
        "MusicBrainz Album Id",
    ),
    FieldSpec(
        "musicbrainz_releasegroupid",
        "",
        "musicbrainz_releasegroupid",
        "",
        "MusicBrainz Release Group Id",
    ),
    FieldSpec("release_country", "", "releasecountry", "", "MusicBrainz Album Release Country"),
    FieldSpec("release_status", "", "releasestatus", "", "MusicBrainz Album Status"),
    FieldSpec("release_type", "", "releasetype", "", "MusicBrainz Album Type"),
    FieldSpec("language", "TLAN", "language", "", "Language"),
    FieldSpec("script", "", "script", "", "Script"),
    FieldSpec("tag", "", "tag", "", "WM/Category", multi=True),
)

FIELD_BY_NAME = {field.canonical: field for field in FIELDS}
CANONICAL_FIELD_NAMES = frozenset(
    {
        *FIELD_BY_NAME,
        "tracknumber",
        "tracktotal",
        "discnumber",
        "disctotal",
    }
)
MULTI_VALUE_FIELDS = frozenset(field.canonical for field in FIELDS if field.multi)


def expected_metadata(values: Mapping[str, Any]) -> CanonicalMetadata:
    return CanonicalMetadata(
        {key: value for key, value in values.items() if key in CANONICAL_FIELD_NAMES}
    )


def value_list(value: Any, *, split_slash: bool = False) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        raw = [str(item) for item in value]
    else:
        raw = [str(value)]
    if split_slash:
        raw = [part for item in raw for part in item.split("/")]
    return [item.strip() for item in raw if item.strip()]


def scalar_text(value: Any) -> str:
    return ", ".join(value_list(value))


def pair_text(number: Any, total: Any) -> str:
    number_text = scalar_text(number)
    total_text = scalar_text(total)
    if total_text:
        return f"{number_text or '0'}/{total_text}"
    return number_text


def split_pair(value: Any) -> tuple[str, str]:
    text = scalar_text(value)
    number, separator, total = text.partition("/")
    return number.strip(), total.strip() if separator else ""


def metadata_matches(
    expected: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    for key, value in expected.items():
        actual = current.get(key, "")
        if key in MULTI_VALUE_FIELDS:
            expected_values = {item.casefold() for item in value_list(value, split_slash=True)}
            actual_values = {item.casefold() for item in value_list(actual, split_slash=True)}
            if expected_values != actual_values:
                return False
        elif scalar_text(actual) != scalar_text(value):
            return False
    return True


__all__ = [
    "CANONICAL_FIELD_NAMES",
    "FIELDS",
    "FIELD_BY_NAME",
    "MULTI_VALUE_FIELDS",
    "FieldSpec",
    "expected_metadata",
    "metadata_matches",
    "pair_text",
    "scalar_text",
    "split_pair",
    "value_list",
]
