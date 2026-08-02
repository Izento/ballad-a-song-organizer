"""Thin media reader dispatching to container adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.metadata import ArtworkDescriptor, CanonicalMetadata
from .adapters import asf, id3, mp4, vorbis


@dataclass(frozen=True)
class MediaRead:
    path: str
    status: str
    container: str = ""
    tags: CanonicalMetadata = field(default_factory=CanonicalMetadata)
    artwork: ArtworkDescriptor | None = None
    duration: float | None = None
    bitrate: int | None = None
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", CanonicalMetadata.coerce(self.tags))
        object.__setattr__(
            self,
            "artwork",
            (
                self.artwork
                if isinstance(self.artwork, ArtworkDescriptor)
                else ArtworkDescriptor.from_dict(self.artwork)
            ),
        )

    @property
    def usable(self) -> bool:
        return self.status in {"ok", "empty"}


def _adapter(audio: Any):
    container = type(audio).__name__.casefold()
    if container in {"mp3", "id3"}:
        return id3
    if container in {"flac", "oggvorbis"}:
        return vorbis
    if container in {"mp4", "mp4cover"}:
        return mp4
    if container == "asf":
        return asf
    tags = getattr(audio, "tags", None)
    if tags is not None and hasattr(tags, "getall"):
        return id3
    return None


def read_media(path: str) -> MediaRead:
    """Read technical details and canonical metadata from one media file."""
    try:
        import mutagen
    except ImportError as exc:
        return MediaRead(path=path, status="error", error=str(exc))

    try:
        audio = mutagen.File(path)
    except PermissionError as exc:
        return MediaRead(path=path, status="permission_denied", error=str(exc))
    except OSError as exc:
        return MediaRead(path=path, status="unreadable", error=str(exc))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return MediaRead(path=path, status="malformed", error=str(exc))
    if audio is None:
        return MediaRead(path=path, status="unsupported", error="Unsupported media")

    adapter = _adapter(audio)
    info = getattr(audio, "info", None)
    duration = getattr(info, "length", None)
    bitrate = getattr(info, "bitrate", None)
    if adapter is None:
        tags = CanonicalMetadata()
        artwork = None
    else:
        try:
            tags = CanonicalMetadata(adapter.read_tags(audio))
            artwork = adapter.read_artwork(audio)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            return MediaRead(
                path=path,
                status="malformed",
                container=type(audio).__name__,
                error=str(exc),
            )
    return MediaRead(
        path=path,
        status="ok" if tags else "empty",
        container=type(audio).__name__,
        tags=tags,
        artwork=artwork,
        duration=float(duration) if duration is not None else None,
        bitrate=int(bitrate) if bitrate is not None else None,
    )


def read_front_artwork(path: str) -> tuple[bytes, str] | None:
    """Return embedded front-art bytes for transactional backup."""
    import mutagen

    audio = mutagen.File(path)
    if audio is None:
        return None
    adapter = _adapter(audio)
    if adapter is None:
        return None
    return adapter.read_artwork_data(audio)


__all__ = ["MediaRead", "read_front_artwork", "read_media"]
