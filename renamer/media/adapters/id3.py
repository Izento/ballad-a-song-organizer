"""ID3 reader/writer adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.metadata import ArtworkDescriptor
from ..schema import FIELDS, MULTI_VALUE_FIELDS, pair_text, split_pair, value_list

# ID3v2.3 text frames have no native way to store more than one value, so
# multi-value fields (genre, writer, tag, ...) are joined into a single
# string on write and split back apart on read. "; " is deliberately
# unusual for real tag/credit text -- unlike "/", which collides with
# legitimate compound values such as the MusicBrainz genre "hip-hop/rap".
_JOIN_SEP = "; "


def _split_joined(text: str) -> list[str]:
    if _JOIN_SEP in text:
        return text.split(_JOIN_SEP)
    if "/" in text:
        # Backward compatibility only: files tagged before this fix used
        # "/" as the join separator, which is ambiguous for values that
        # already contain a literal slash. New writes never produce this.
        return text.split("/")
    return [text]


def _frame_values(frame: Any, *, split_multi: bool = False) -> list[str]:
    items = value_list(getattr(frame, "text", frame))
    if not split_multi:
        return items
    return [
        part.strip()
        for item in items
        for part in _split_joined(item)
        if part.strip()
    ]


_MULTI_VALUE_FIELDS_CASEFOLD = frozenset(
    name.casefold() for name in MULTI_VALUE_FIELDS
)


def _custom_values(tags: Any) -> dict[str, list[str]]:
    # Multi-value fields without a dedicated ID3 frame (writer, producer,
    # mixer, tag) are stored in a custom TXXX frame.
    result: dict[str, list[str]] = {}
    for frame in tags.getall("TXXX"):
        description = str(getattr(frame, "desc", "")).casefold()
        if description:
            result[description] = _frame_values(
                frame,
                split_multi=description in _MULTI_VALUE_FIELDS_CASEFOLD,
            )
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
                        split_multi=field.canonical in MULTI_VALUE_FIELDS,
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
        ID3NoHeaderError,
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
        # Pre-join multi-value fields ourselves into a single string rather
        # than handing mutagen a multi-item text list: mutagen's own v2.3
        # join (v23_sep, default "/") would reintroduce the same "/"
        # collision this separator was chosen to avoid.
        text = _JOIN_SEP.join(entries) if field.canonical in MULTI_VALUE_FIELDS else (
            entries[0] if entries else ""
        )
        if field.id3:
            frame_type = frame_types[field.id3]
            if text:
                tags.setall(
                    field.id3,
                    [frame_type(encoding=3, text=[text])],
                )
            else:
                tags.delall(field.id3)
            continue
        description = field.canonical.upper()
        if text:
            tags.setall(
                f"TXXX:{description}",
                [TXXX(encoding=3, desc=description, text=[text])],
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
    # v1=0: strip/never regenerate the legacy ID3v1 trailer. Mutagen's
    # default (v1=1) silently rewrites an existing v1 tag from whatever the
    # current ID3v2 frames say every time we save -- and when a field like
    # genre isn't set at the v2 level, it writes an "unmapped" v1 genre
    # byte (255) that some players (observed: Winamp) misrender as an
    # unrelated genre name instead of "no genre". ID3v2 is authoritative
    # for every field mutagen/Ballad reads, so the v1 trailer is pure
    # legacy cruft that only exists to cause exactly this kind of bug.
    tags.save(path, v2_version=3, v1=0)


__all__ = ["read_artwork", "read_artwork_data", "read_tags", "write"]
