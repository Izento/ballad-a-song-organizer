"""MP4/M4A atom adapter."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ...domain.metadata import ArtworkDescriptor
from ..schema import FIELDS, split_pair, value_list

_FREEFORM_PREFIX = "----:com.apple.iTunes:"


def _atom_values(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    return [
        (
            bytes(item).decode("utf-8", errors="replace")
            if isinstance(item, (bytes, bytearray))
            else str(item)
        ).strip()
        for item in values
        if item not in (None, "")
    ]


def _target(field) -> str:
    return field.mp4 or f"{_FREEFORM_PREFIX}{field.canonical.upper()}"


def read_tags(audio: Any) -> dict[str, Any]:
    tags = getattr(audio, "tags", None)
    if not tags:
        return {}
    result: dict[str, Any] = {}
    for field in FIELDS:
        values = _atom_values(tags.get(_target(field), []))
        if values:
            result[field.canonical] = values if field.multi else values[0]
    for atom, number_key, total_key in (
        ("trkn", "tracknumber", "tracktotal"),
        ("disk", "discnumber", "disctotal"),
    ):
        pairs = tags.get(atom) or []
        if not pairs:
            continue
        number, total = pairs[0]
        if number:
            result[number_key] = str(number)
        if total:
            result[total_key] = str(total)
    return result


def read_artwork_data(audio: Any) -> tuple[bytes, str] | None:
    tags = getattr(audio, "tags", None)
    covers = tags.get("covr") if tags else None
    if not covers:
        return None
    cover = covers[0]
    data = bytes(cover)
    image_format = getattr(cover, "imageformat", None)
    mime_type = "image/png" if image_format == 14 else "image/jpeg"
    return data, mime_type


def read_artwork(audio: Any) -> ArtworkDescriptor | None:
    image = read_artwork_data(audio)
    if image is None:
        return None
    data, mime_type = image
    return ArtworkDescriptor(
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        mime_type=mime_type,
    )


def write(
    path: str,
    values: Mapping[str, Any],
    image: tuple[bytes, str] | None,
    *,
    replace_artwork: bool = False,
) -> None:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(path)
    replace_artwork = replace_artwork or image is not None
    if audio.tags is None:
        audio.add_tags()
    for field in FIELDS:
        if field.canonical not in values:
            continue
        target = _target(field)
        entries = value_list(values[field.canonical])
        if entries:
            audio.tags[target] = (
                [entry.encode("utf-8") for entry in entries]
                if target.startswith(_FREEFORM_PREFIX)
                else entries
            )
        else:
            audio.tags.pop(target, None)

    for atom, number_key, total_key in (
        ("trkn", "tracknumber", "tracktotal"),
        ("disk", "discnumber", "disctotal"),
    ):
        if number_key not in values and total_key not in values:
            continue
        number, _unused = split_pair(values.get(number_key))
        total, _unused_total = split_pair(values.get(total_key))
        audio.tags[atom] = [(int(number or 0), int(total or 0))]

    if replace_artwork:
        audio.tags.pop("covr", None)
    if image:
        data, mime_type = image
        image_format = (
            MP4Cover.FORMAT_PNG
            if mime_type == "image/png"
            else MP4Cover.FORMAT_JPEG
        )
        audio.tags["covr"] = [MP4Cover(data, imageformat=image_format)]
    audio.save()


__all__ = ["read_artwork", "read_artwork_data", "read_tags", "write"]
