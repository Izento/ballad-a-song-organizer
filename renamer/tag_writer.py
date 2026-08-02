"""Compatibility facade for canonical media tag writing."""

from .media.legacy_filename import parse_stem
from .media.writer import (
    _write_asf,
    _write_mp3,
    _write_mp4,
    _write_vorbis,
    supports_tag_writing,
    write_tags_to_file,
)

__all__ = [
    "_write_asf",
    "_write_mp3",
    "_write_mp4",
    "_write_vorbis",
    "parse_stem",
    "supports_tag_writing",
    "write_tags_to_file",
]
