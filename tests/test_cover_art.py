# pylint: disable=import-error

from types import SimpleNamespace

from renamer.cache import EnrichmentCache
from renamer import cover_art


class _Response:
    def __init__(self, data: bytes, mime_type: str):
        self._data = data
        self.headers = SimpleNamespace(
            get=lambda key: str(len(data)) if key == "Content-Length" else None,
            get_content_type=lambda: mime_type,
        )

    def read(self, _limit: int) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_cover_art_is_cached_by_digest_and_verifiable(tmp_path, monkeypatch):
    cache = EnrichmentCache(tmp_path / "enrichment.sqlite3")
    monkeypatch.setattr(cover_art, "enrichment_cache", lambda: cache)
    monkeypatch.setattr(
        cover_art,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"\xff\xd8\xffcover", "image/jpeg"),
    )

    artwork = cover_art.download_front_art("release-id")

    assert artwork is not None
    assert cover_art.verify_artwork(artwork)
    assert cover_art.download_front_art("release-id") == artwork


def test_cover_art_rejects_unsupported_image_content(tmp_path, monkeypatch):
    cache = EnrichmentCache(tmp_path / "enrichment.sqlite3")
    monkeypatch.setattr(cover_art, "enrichment_cache", lambda: cache)
    monkeypatch.setattr(
        cover_art,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"<html>", "text/html"),
    )

    assert cover_art.download_front_art("release-id") is None
