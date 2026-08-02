"""Typed identity evidence shared by extraction and enrichment."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping


class Confidence(StrEnum):
    UNRESOLVED = "unresolved"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_CONFIDENCE_ORDER = tuple(Confidence)


def weakest_confidence(*values: "Confidence | str") -> Confidence:
    """Return the most conservative of several confidence signals.

    A downstream step succeeding cleanly doesn't make an upstream, shakier
    signal any more certain -- MusicBrainz can resolve one unambiguous
    official release for whatever recording AcoustID handed it, but that
    says nothing about how sure the fingerprint match itself was. Without
    this, a shaky identification paired with a clean release lookup was
    displayed as "high" trust, hiding exactly the failure mode worth
    flagging: a wrong recording match that happens to have tidy metadata.
    """
    resolved = (Confidence(value) for value in values)
    return min(resolved, key=_CONFIDENCE_ORDER.index)


class IdentitySource(StrEnum):
    EMBEDDED_MUSICBRAINZ = "embedded_musicbrainz"
    ACOUSTID = "acoustid"
    MUSICBRAINZ = "musicbrainz"
    TAGS = "tags"
    FILENAME = "filename"


class ExtractionStrategy(StrEnum):
    UNKNOWN = ""
    TAG_BASED = "tag_based"
    FILENAME = "filename_norm"
    ACOUSTID = "acoustid"
    MUSICBRAINZ = "musicbrainz"
    OCREMIX_TAGGED = "ocremix_tagged"
    OCREMIX_OLD_TAGS = "ocremix_old_tags"
    OCREMIX_FILENAME = "ocremix_filename"


@dataclass(frozen=True)
class _FrozenMap:
    items: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class _FrozenList:
    items: tuple[Any, ...]


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMap(
            tuple((str(key), _freeze_json(item)) for key, item in value.items())
        )
    if isinstance(value, (list, tuple)):
        return _FrozenList(tuple(_freeze_json(item) for item in value))
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, _FrozenMap):
        return {key: _thaw_json(item) for key, item in value.items}
    if isinstance(value, _FrozenList):
        return [_thaw_json(item) for item in value.items]
    return value


class Evidence(Mapping[str, Any]):
    """Deeply immutable provider evidence with JSON-safe conversion."""

    __slots__ = ("_items", "_lookup")

    def __init__(self, value: Mapping[str, Any] | None = None):
        self._items = tuple(
            (str(key), _freeze_json(item))
            for key, item in (value or {}).items()
        )
        self._lookup = dict(self._items)

    @classmethod
    def coerce(cls, value: "Evidence | Mapping[str, Any] | None") -> "Evidence":
        return value if isinstance(value, cls) else cls(value)

    def __getitem__(self, key: str) -> Any:
        return _thaw_json(self._lookup[key])

    def __iter__(self) -> Iterator[str]:
        return (key for key, _item in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and self.to_dict() == dict(other)

    def to_dict(self) -> dict[str, Any]:
        return {key: _thaw_json(item) for key, item in self._items}


@dataclass(frozen=True)
class RecordingIdentity:
    """Exact recording identity or source-song evidence for a derivative."""

    artist: str = ""
    title: str = ""
    exact_recording_id: str = ""
    derived_from_recording_id: str = ""
    acoustid_score: float | None = None
    confidence: Confidence = Confidence.UNRESOLVED
    warnings: tuple[str, ...] = ()
    provenance: tuple[IdentitySource | str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", Confidence(self.confidence))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "provenance", tuple(self.provenance))

    @property
    def resolved_recording_id(self) -> str:
        return self.exact_recording_id or self.derived_from_recording_id

    @property
    def is_derivative(self) -> bool:
        return bool(self.derived_from_recording_id and not self.exact_recording_id)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RecordingIdentity":
        return cls(
            artist=str(value.get("artist") or ""),
            title=str(value.get("title") or ""),
            exact_recording_id=str(value.get("exact_recording_id") or ""),
            derived_from_recording_id=str(
                value.get("derived_from_recording_id") or ""
            ),
            acoustid_score=value.get("acoustid_score"),
            confidence=Confidence(str(value.get("confidence") or "unresolved")),
            warnings=tuple(value.get("warnings") or ()),
            provenance=tuple(value.get("provenance") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["confidence"] = self.confidence.value
        result["provenance"] = [
            value.value if isinstance(value, IdentitySource) else str(value)
            for value in self.provenance
        ]
        return result


@dataclass(frozen=True)
class ExtractedTrack:
    """Immutable identity extracted from one local media file."""

    path: str
    ext: str
    artist: str = ""
    title: str = ""
    feat_artists: tuple[str, ...] = ()
    is_ocremix: bool = False
    game: str = ""
    remixers: tuple[str, ...] = ()
    strategy: ExtractionStrategy = ExtractionStrategy.UNKNOWN
    needs_lookup: bool = False
    skip_reason: str = ""
    mb_album: str = ""
    mb_track_num: int = 0
    duration: float | None = None
    bitrate: int | None = None
    acoustid_score: float | None = None
    acoustid_recording_id: str = ""
    exact_recording_id: str = ""
    derived_from_recording_id: str = ""
    version_warning: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "feat_artists", tuple(self.feat_artists))
        object.__setattr__(self, "remixers", tuple(self.remixers))
        object.__setattr__(self, "strategy", ExtractionStrategy(self.strategy))


__all__ = [
    "Confidence",
    "Evidence",
    "ExtractedTrack",
    "ExtractionStrategy",
    "IdentitySource",
    "RecordingIdentity",
    "weakest_confidence",
]
