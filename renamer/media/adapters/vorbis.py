"""FLAC and Ogg Vorbis comment adapter."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...domain.metadata import ArtworkDescriptor
from ..schema import FIELDS, value_list


def read_tags(audio: Any) -> dict[str, Any]:
    tags = getattr(audio, "tags", None)
    if not tags:
        return {}
    result: dict[str, Any] = {}
    for field in FIELDS:
        if not field.vorbis:
            continue
        values = value_list(tags.get(field.vorbis))
        if values:
            result[field.canonical] = values if field.multi else values[0]
    for field in ("tracknumber", "tracktotal", "discnumber", "disctotal"):
        values = value_list(tags.get(field))
        if values:
            result[field] = values[0]
    return result


def _descriptor(data: bytes, mime_type: str) -> ArtworkDescriptor:
    return ArtworkDescriptor(
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        mime_type=mime_type or "application/octet-stream",
    )


def read_artwork_data(audio: Any) -> tuple[bytes, str] | None:
    pictures = getattr(audio, "pictures", None)
    if pictures:
        front = next(
            (
                picture
                for picture in pictures
                if getattr(picture, "type", None) == 3
            ),
            pictures[0],
        )
        return bytes(front.data), str(front.mime)

    tags = getattr(audio, "tags", None)
    encoded_values = tags.get("metadata_block_picture") if tags else None
    if not encoded_values:
        return None
    from mutagen.flac import Picture

    try:
        picture = Picture(base64.b64decode(encoded_values[0]))
    except (TypeError, ValueError):
        return None
    return bytes(picture.data), str(picture.mime)


def read_artwork(audio: Any) -> ArtworkDescriptor | None:
    image = read_artwork_data(audio)
    if image is None:
        return None
    return _descriptor(*image)


def write(
    path: str,
    values: Mapping[str, Any],
    image: tuple[bytes, str] | None,
    *,
    replace_artwork: bool = False,
) -> None:
    replace_artwork = replace_artwork or image is not None
    extension = Path(path).suffix.casefold()
    if extension == ".flac":
        from mutagen.flac import FLAC

        audio = FLAC(path)
    else:
        from mutagen.oggvorbis import OggVorbis

        audio = OggVorbis(path)

    for field in FIELDS:
        if not field.vorbis or field.canonical not in values:
            continue
        entries = value_list(values[field.canonical])
        if entries:
            audio[field.vorbis] = entries
        elif field.vorbis in audio:
            del audio[field.vorbis]
    for field in ("tracknumber", "tracktotal", "discnumber", "disctotal"):
        if field not in values:
            continue
        entries = value_list(values[field])
        if entries:
            audio[field] = entries
        elif field in audio:
            del audio[field]

    if replace_artwork:
        if extension == ".flac":
            retained = [
                current
                for current in audio.pictures
                if current.type != 3
            ]
            audio.clear_pictures()
            for current in retained:
                audio.add_picture(current)
        else:
            audio.pop("metadata_block_picture", None)
    if image:
        from mutagen.flac import Picture

        data, mime_type = image
        picture = Picture()
        picture.type = 3
        picture.mime = mime_type
        picture.desc = "Front cover"
        picture.data = data
        if extension == ".flac":
            audio.add_picture(picture)
        else:
            audio["metadata_block_picture"] = [
                base64.b64encode(picture.write()).decode("ascii")
            ]
    audio.save()


__all__ = ["read_artwork", "read_artwork_data", "read_tags", "write"]
