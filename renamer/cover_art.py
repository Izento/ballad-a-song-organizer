"""Cover Art Archive retrieval with bounded, content-addressed local storage."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .cache import enrichment_cache
from .domain.metadata import StagedArtwork
from .online import Provider, ProviderError, RateLimiter, RequestPolicy


_MAX_ART_BYTES = 5 * 1024 * 1024
_ACCEPTED_MIME_TYPES = {"image/jpeg", "image/png"}
_REQUEST_POLICY = RequestPolicy(
    provider=Provider.COVER_ART_ARCHIVE,
    limiter=RateLimiter(1.1),
    retries=1,
)


ArtworkRef = StagedArtwork


def _sniff_mime(data: bytes, header: str) -> str | None:
    header = header.split(";", 1)[0].casefold()
    if header in _ACCEPTED_MIME_TYPES:
        return header
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _suffix(mime_type: str) -> str:
    if mime_type == "image/jpeg":
        return ".jpg"
    return mimetypes.guess_extension(mime_type) or ".img"


def download_front_art(
    release_id: str,
    *,
    timeout: int = 20,
) -> ArtworkRef | None:
    """Fetch a bounded 500px front image for one verified MusicBrainz release."""
    if not release_id:
        return None
    cache = enrichment_cache()
    cache_key = f"{release_id}:front-500"
    cached = cache.get("cover-art", cache_key)
    if cached and Path(cached["path"]).is_file():
        return ArtworkRef(**cached)

    source_url = f"https://coverartarchive.org/release/{release_id}/front-500"
    request = Request(source_url, headers={"User-Agent": "Ballad/1.0"})
    def fetch() -> tuple[bytes, str | None]:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > _MAX_ART_BYTES:
                return b"", None
            data = response.read(_MAX_ART_BYTES + 1)
            if len(data) > _MAX_ART_BYTES:
                return b"", None
            return data, _sniff_mime(
                data,
                response.headers.get_content_type(),
            )

    try:
        data, mime_type = _REQUEST_POLICY.request(
            fetch,
            transient_errors=(HTTPError, URLError, OSError, ValueError),
        )
    except ProviderError:
        return None
    if not data or not mime_type:
        return None

    asset = cache.put_asset(data, _suffix(mime_type))
    artwork = ArtworkRef(
        sha256=str(asset["sha256"]),
        size=int(asset["size"]),
        mime_type=mime_type,
        path=str(asset["path"]),
        release_id=release_id,
        source_url=source_url,
    )
    cache.set("cover-art", cache_key, artwork.to_dict())
    return artwork


def verify_artwork(artwork: ArtworkRef) -> bool:
    """Verify a staged asset still exists and matches the review-plan digest."""
    path = Path(artwork.path)
    if not path.is_file() or path.stat().st_size != artwork.size:
        return False
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest() == artwork.sha256


__all__ = ["ArtworkRef", "download_front_art", "verify_artwork"]
