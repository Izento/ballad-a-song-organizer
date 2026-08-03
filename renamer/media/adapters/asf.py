"""ASF/WMA attribute adapter."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ...domain.metadata import ArtworkDescriptor
from ..schema import FIELDS, value_list


def _attribute_values(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    result = []
    for item in values:
        raw = getattr(item, "value", item)
        if isinstance(raw, bytes):
            continue
        text = str(raw).strip()
        if text:
            result.append(text)
    return result


def read_tags(audio: Any) -> dict[str, Any]:
    tags = getattr(audio, "tags", None)
    if not tags:
        return {}
    result: dict[str, Any] = {}
    for field in FIELDS:
        if not field.asf:
            continue
        values = _attribute_values(tags.get(field.asf, []))
        if values:
            result[field.canonical] = values if field.multi else values[0]
    for canonical, target in (
        ("tracknumber", "WM/TrackNumber"),
        ("tracktotal", "WM/TrackTotal"),
        ("discnumber", "WM/PartOfSet"),
        ("disctotal", "WM/PartOfSetTotal"),
    ):
        values = _attribute_values(tags.get(target, []))
        if values:
            result[canonical] = values[0]
    return result


def _read_utf16_field(data: bytes, offset: int) -> tuple[str, int]:
    for end in range(offset, len(data) - 1, 2):
        if data[end : end + 2] == b"\x00\x00":
            return data[offset:end].decode("utf-16-le", errors="replace"), end + 2
    raise ValueError("Unterminated ASF picture field")


def _picture_bytes(attribute: Any) -> tuple[bytes, str] | None:
    payload = bytes(getattr(attribute, "value", attribute))
    if len(payload) < 5:
        return None
    image_size = int.from_bytes(payload[1:5], "little")
    try:
        mime_type, offset = _read_utf16_field(payload, 5)
        _description, offset = _read_utf16_field(payload, offset)
    except ValueError:
        return None
    data = payload[offset : offset + image_size]
    if not data:
        return None
    return data, mime_type


def read_artwork_data(audio: Any) -> tuple[bytes, str] | None:
    tags = getattr(audio, "tags", None)
    pictures = tags.get("WM/Picture") if tags else None
    if not pictures:
        return None
    for picture in pictures:
        parsed = _picture_bytes(picture)
        if parsed is None:
            continue
        return parsed
    return None


def read_artwork(audio: Any) -> ArtworkDescriptor | None:
    image = read_artwork_data(audio)
    if image is None:
        return None
    data, mime_type = image
    return ArtworkDescriptor(
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        mime_type=mime_type or "application/octet-stream",
    )


def _write_attributes(
    audio: Any,
    values: Mapping[str, Any],
    targets: tuple[tuple[str, str], ...],
    attribute_type: Any,
    *,
    first_only: bool = False,
) -> None:
    for canonical, target in targets:
        if canonical not in values:
            continue
        entries = value_list(values[canonical])
        if entries:
            entries = entries[:1] if first_only else entries
            audio[target] = [attribute_type(entry) for entry in entries]
        elif target in audio:
            del audio[target]


def write(
    path: str,
    values: Mapping[str, Any],
    image: tuple[bytes, str] | None,
    *,
    replace_artwork: bool = False,
) -> None:
    from mutagen.asf import ASF, ASFByteArrayAttribute, ASFUnicodeAttribute

    audio = ASF(path)
    replace_artwork = replace_artwork or image is not None
    field_targets = tuple((field.canonical, field.asf) for field in FIELDS if field.asf)
    _write_attributes(audio, values, field_targets, ASFUnicodeAttribute)
    number_targets = (
        ("tracknumber", "WM/TrackNumber"),
        ("tracktotal", "WM/TrackTotal"),
        ("discnumber", "WM/PartOfSet"),
        ("disctotal", "WM/PartOfSetTotal"),
    )
    _write_attributes(
        audio,
        values,
        number_targets,
        ASFUnicodeAttribute,
        first_only=True,
    )

    if replace_artwork:
        audio.pop("WM/Picture", None)
    if image:
        data, mime_type = image
        encoded_mime = mime_type.encode("utf-16-le") + b"\x00\x00"
        encoded_description = "Front cover".encode("utf-16-le") + b"\x00\x00"
        payload = b"\x03" + len(data).to_bytes(4, "little") + encoded_mime
        payload += encoded_description + data
        audio["WM/Picture"] = [ASFByteArrayAttribute(payload)]
    audio.save()


__all__ = ["read_artwork", "read_artwork_data", "read_tags", "write"]
