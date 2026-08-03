"""Canonical media inspection and writing facade."""

from __future__ import annotations

from .reader import MediaRead, read_front_artwork, read_media
from .writer import supports_tag_writing, write_tags_to_file

__all__ = [
    "MediaRead",
    "read_front_artwork",
    "read_media",
    "supports_tag_writing",
    "write_tags_to_file",
]
