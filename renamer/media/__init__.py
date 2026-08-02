"""Canonical media inspection and writing facade."""

from __future__ import annotations

from typing import Mapping

from .reader import MediaRead, read_front_artwork, read_media
from .writer import supports_tag_writing, write_tags_to_file


def canonical_to_id3(tags: Mapping[str, str]) -> dict[str, str]:
    """Translate display fields for legacy extraction callers."""
    mapping = {
        "artist": "TPE1",
        "title": "TIT2",
        "album": "TALB",
        "album_artist": "TPE2",
        "grouping": "TIT1",
        "subtitle": "TIT3",
    }
    return {
        mapping[key]: value
        for key, value in tags.items()
        if key in mapping and value
    }


__all__ = [
    "MediaRead",
    "canonical_to_id3",
    "read_front_artwork",
    "read_media",
    "supports_tag_writing",
    "write_tags_to_file",
]
