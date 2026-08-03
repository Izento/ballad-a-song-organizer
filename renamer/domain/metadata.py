"""Immutable canonical metadata and artwork value objects."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

MetadataScalar = str | int | float | bool
MetadataInput = Mapping[str, Any] | None


def _freeze_value(value: Any) -> MetadataScalar | tuple[str, ...]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value if str(item))
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _public_value(value: MetadataScalar | tuple[str, ...]) -> Any:
    return list(value) if isinstance(value, tuple) else value


class CanonicalMetadata(Mapping[str, Any]):
    """A deeply immutable metadata mapping with copy-on-read list values."""

    __slots__ = ("_items", "_lookup")

    def __init__(self, values: MetadataInput = None):
        items = tuple((str(key), _freeze_value(value)) for key, value in (values or {}).items())
        self._items = items
        self._lookup = dict(items)

    @classmethod
    def coerce(cls, values: CanonicalMetadata | MetadataInput) -> CanonicalMetadata:
        return values if isinstance(values, cls) else cls(values)

    def __getitem__(self, key: str) -> Any:
        return _public_value(self._lookup[key])

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"CanonicalMetadata({self.to_dict()!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self.to_dict() == {
                str(key): _public_value(_freeze_value(value)) for key, value in other.items()
            }
        return False

    def __hash__(self) -> int:
        return hash(self._items)

    def to_dict(self) -> dict[str, Any]:
        return {key: _public_value(value) for key, value in self._items}

    def merged(self, values: MetadataInput) -> CanonicalMetadata:
        merged = self.to_dict()
        merged.update(values or {})
        return CanonicalMetadata(merged)


@dataclass(frozen=True)
class ArtworkDescriptor(Mapping[str, Any]):
    """Digest and media details for artwork already embedded in a file."""

    sha256: str
    size: int
    mime_type: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> ArtworkDescriptor | None:
        if not value:
            return None
        if value.get("path"):
            return StagedArtwork.from_dict(value)
        return cls(
            sha256=str(value.get("sha256") or ""),
            size=int(value.get("size") or 0),
            mime_type=str(value.get("mime_type") or "application/octet-stream"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "mime_type": self.mime_type,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass(frozen=True)
class StagedArtwork(ArtworkDescriptor):
    """A verified, content-addressed artwork asset awaiting application."""

    path: str
    release_id: str = ""
    source_url: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> StagedArtwork | None:
        if not value:
            return None
        return cls(
            path=str(value.get("path") or ""),
            sha256=str(value.get("sha256") or ""),
            size=int(value.get("size") or 0),
            mime_type=str(value.get("mime_type") or "application/octet-stream"),
            release_id=str(value.get("release_id") or ""),
            source_url=str(value.get("source_url") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            **super().to_dict(),
            "release_id": self.release_id,
            "source_url": self.source_url,
        }


def artwork_to_dict(
    artwork: ArtworkDescriptor | StagedArtwork | Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if artwork is None:
        return None
    if isinstance(artwork, (ArtworkDescriptor, StagedArtwork)):
        return artwork.to_dict()
    return dict(artwork)


__all__ = [
    "ArtworkDescriptor",
    "CanonicalMetadata",
    "MetadataInput",
    "StagedArtwork",
    "artwork_to_dict",
]
