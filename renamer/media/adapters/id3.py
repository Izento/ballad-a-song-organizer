"""ID3 reader/writer adapter."""

from __future__ import annotations

from typing import Any, Mapping

from ...domain.metadata import ArtworkDescriptor
from ..schema import FIELDS, MULTI_VALUE_FIELDS, pair_text, split_pair, value_list


def _frame_values(frame: Any, *, split_slash: bool = False) -> list[str]:
    return value_list(getattr(frame, "text", frame), split_slash=split_slash)


def _custom_values(tags: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for frame in tags.getall("TXXX"):
        description = str(getattr(frame, "desc", "")).casefold()
        if description:
            result[description] = _frame_values(frame)
    return result


def read_tags(audio: Any) -> dict[str, Any]:
    tags = getattr(audio, "tags", None)
    if not tags:
        return {}
    result: dict[str, Any] = {}
    custom = _custom_values(tags)
    for field in FIELDS:
        values: list[str] = []
        if field.id3:
            frames = tags.getall(field.id3)
            for frame in frames:
                values.extend(
                    _frame_values(
                        frame,
                        split_slash=field.canonical in MULTI_VALUE_FIELDS,
                    )
                )
        else:
            values = custom.get(field.canonical.casefold(), [])
        if values:
            result[field.canonical] = values if field.multi else values[0]

    for canonical, frame_id in (
        ("tracknumber", "TRCK"),
        ("discnumber", "TPOS"),
    ):
        frames = tags.getall(frame_id)
        if not frames:
            continue
        number, total = split_pair(_frame_values(frames[0]))
        if number:
            result[canonical] = number
        if total:
            result["tracktotal" if canonical == "tracknumber" else "disctotal"] = total
    return result


def read_artwork_data(audio: Any) -> tuple[bytes, str] | None:
    tags = getattr(audio, "tags", None)
    if not tags:
        return None
    pictures = tags.getall("APIC")
    front = next(
        (
            picture
            for picture in pictures
            if getattr(picture, "type", None) == 3
        ),
        pictures[0] if pictures else None,
    )
    if front is None:
        return None
    return (
        bytes(front.data),
        str(getattr(front, "mime", "") or "application/octet-stream"),
    )


def read_artwork(audio: Any) -> ArtworkDescriptor | None:
    image = read_artwork_data(audio)
    if image is None:
        return None
    import hashlib

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
    replace_artwork = replace_artwork or image is not None
    from mutagen.id3 import (
        APIC,
        ID3,
        ID3NoHeaderError,
        TALB,
        TCOM,
        TCON,
        TDRC,
        TEXT,
        TIT1,
        TIT2,
        TIT3,
        TLAN,
        TPE1,
        TPE2,
        TPE3,
        TPE4,
        TPOS,
        TPUB,
        TRCK,
        TSRC,
        TXXX,
    )

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    frame_types = {
        "TPE1": TPE1,
        "TIT2": TIT2,
        "TALB": TALB,
        "TPE2": TPE2,
        "TIT1": TIT1,
        "TIT3": TIT3,
        "TDRC": TDRC,
        "TCON": TCON,
        "TCOM": TCOM,
        "TEXT": TEXT,
        "TPE3": TPE3,
        "TPE4": TPE4,
        "TPUB": TPUB,
        "TSRC": TSRC,
        "TLAN": TLAN,
    }
    for field in FIELDS:
        if field.canonical not in values:
            continue
        entries = value_list(values[field.canonical])
        if field.id3:
            frame_type = frame_types[field.id3]
            if entries:
                tags.setall(
                    field.id3,
                    [frame_type(encoding=3, text=entries)],
                )
            else:
                tags.delall(field.id3)
            continue
        description = field.canonical.upper()
        if entries:
            tags.setall(
                f"TXXX:{description}",
                [TXXX(encoding=3, desc=description, text=entries)],
            )
        else:
            tags.delall(f"TXXX:{description}")

    for number_key, total_key, frame_id, frame_type in (
        ("tracknumber", "tracktotal", "TRCK", TRCK),
        ("discnumber", "disctotal", "TPOS", TPOS),
    ):
        if number_key not in values and total_key not in values:
            continue
        text = pair_text(values.get(number_key), values.get(total_key))
        if text:
            tags.setall(frame_id, [frame_type(encoding=3, text=[text])])
        else:
            tags.delall(frame_id)

    if replace_artwork:
        retained = [
            frame
            for frame in tags.getall("APIC")
            if getattr(frame, "type", None) != 3
        ]
        tags.delall("APIC")
        for frame in retained:
            tags.add(frame)
        if image:
            data, mime_type = image
            tags.add(
                APIC(
                    encoding=3,
                    mime=mime_type,
                    type=3,
                    desc="Front cover",
                    data=data,
                )
            )
    tags.save(path, v2_version=3)


__all__ = ["read_artwork", "read_artwork_data", "read_tags", "write"]
