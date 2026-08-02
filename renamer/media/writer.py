"""Thin canonical media writer dispatching to container adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from ..domain.metadata import ArtworkDescriptor, StagedArtwork
from .adapters import asf, id3, mp4, vorbis
from .legacy_filename import parse_stem, parsed_tag_values
from .reader import read_media
from .schema import expected_metadata, metadata_matches


_ADAPTERS = {
    ".mp3": id3,
    ".flac": vorbis,
    ".ogg": vorbis,
    ".m4a": mp4,
    ".aac": mp4,
    ".mp4": mp4,
    ".wma": asf,
}


def supports_tag_writing(path: str) -> bool:
    return Path(path).suffix.casefold() in _ADAPTERS


def _artwork_bytes(
    artwork: ArtworkDescriptor | StagedArtwork | Mapping[str, Any] | None,
) -> tuple[bytes, str] | None:
    if not artwork:
        return None
    source = Path(str(artwork.get("path") or ""))
    if not source.is_file():
        raise ValueError("Staged artwork file is missing")
    data = source.read_bytes()
    expected_size = int(artwork.get("size") or 0)
    expected_digest = str(artwork.get("sha256") or "")
    if expected_size and len(data) != expected_size:
        raise ValueError("Staged artwork size changed")
    if expected_digest and hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValueError("Staged artwork digest changed")
    return data, str(artwork.get("mime_type") or "image/jpeg")


def _legacy_expected(path: str) -> dict[str, Any] | None:
    parsed = parse_stem(Path(path).stem)
    return parsed_tag_values(parsed) if parsed is not None else None


def write_tags_to_file(
    path: str,
    expected_tags: Mapping[str, Any] | None = None,
    artwork: ArtworkDescriptor | StagedArtwork | Mapping[str, Any] | None = None,
    *,
    remove_artwork: bool = False,
) -> dict[str, str]:
    """Write canonical tags and optional front artwork to one media file."""
    extension = Path(path).suffix.casefold()
    adapter = _ADAPTERS.get(extension)
    if adapter is None:
        return {
            "status": "skipped",
            "reason": f"Unsupported format: {extension}",
        }
    media = read_media(path)
    if not media.usable:
        return {
            "status": "skipped",
            "reason": f"{media.status}: {media.error or 'cannot read media'}",
        }
    if extension == ".aac" and media.container not in {"MP4", "MP4Cover"}:
        return {
            "status": "skipped",
            "reason": "Raw AAC is not a writable MP4 container",
        }

    if expected_tags is None:
        expected_tags = _legacy_expected(path)
        if expected_tags is None:
            return {
                "status": "skipped",
                "reason": "Filename not in expected format",
            }
    expected = expected_metadata(expected_tags)
    if not expected and not artwork and not remove_artwork:
        return {
            "status": "skipped",
            "reason": "No supported tag values to write",
        }
    artwork_matches = (
        media.artwork is None
        if remove_artwork
        else artwork is None
        or (
            media.artwork is not None
            and media.artwork.sha256 == str(artwork.get("sha256") or "")
        )
    )
    if metadata_matches(expected, media.tags) and artwork_matches:
        return {"status": "already_ok"}

    try:
        adapter.write(
            path,
            expected,
            _artwork_bytes(artwork),
            replace_artwork=artwork is not None or remove_artwork,
        )
    except Exception as exc:  # Mutagen has container-specific exception families.
        return {"status": "error", "reason": str(exc)}
    return {"status": "updated"}


def _write_mp3(path: str, values: Mapping[str, Any], artwork=None) -> None:
    id3.write(path, expected_metadata(values), _artwork_bytes(artwork))


def _write_vorbis(path: str, values: Mapping[str, Any], artwork=None) -> None:
    vorbis.write(path, expected_metadata(values), _artwork_bytes(artwork))


def _write_mp4(path: str, values: Mapping[str, Any], artwork=None) -> None:
    mp4.write(path, expected_metadata(values), _artwork_bytes(artwork))


def _write_asf(path: str, values: Mapping[str, Any], artwork=None) -> None:
    asf.write(path, expected_metadata(values), _artwork_bytes(artwork))


__all__ = [
    "_write_asf",
    "_write_mp3",
    "_write_mp4",
    "_write_vorbis",
    "parse_stem",
    "supports_tag_writing",
    "write_tags_to_file",
]
