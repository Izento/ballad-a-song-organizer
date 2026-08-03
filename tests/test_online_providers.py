# pylint: disable=import-error

from types import SimpleNamespace

from renamer import cover_art, identification
from renamer.online.cache import EnrichmentCache


def test_cache_round_trips_json_values_and_expiry(tmp_path):
    cache = EnrichmentCache(tmp_path / "enrichment.sqlite3")
    cache.set("recording", "id", {"artist": "Artist", "genres": ["Electronic"]})
    cache.set("negative", "missing", None, ttl_seconds=0)

    assert cache.get("recording", "id") == {
        "artist": "Artist",
        "genres": ["Electronic"],
    }
    assert cache.get("negative", "missing") is None


def test_cache_deduplicates_binary_assets_by_hash(tmp_path):
    cache = EnrichmentCache(tmp_path / "enrichment.sqlite3")

    first = cache.put_asset(b"artwork", ".jpg")
    second = cache.put_asset(b"artwork", ".jpg")

    assert first == second


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


def test_existing_recording_id_is_high_confidence_without_network(tmp_path):
    path = tmp_path / "Artist - Track.mp3"
    path.write_bytes(b"audio")

    result = identification.identify(
        str(path),
        tags={
            "artist": "Artist",
            "title": "Track",
            "musicbrainz_recordingid": "recording-id",
        },
    )

    assert result.exact_recording_id == "recording-id"
    assert result.derived_from_recording_id == ""
    assert result.confidence == "high"


def test_custom_extended_edit_keeps_source_recording_as_derivation(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "ARXMANE x Crazy Mano - Matador De Dragao (Extended).mp3"
    path.write_bytes(b"audio")
    cache = EnrichmentCache(tmp_path / "cache.sqlite3")
    monkeypatch.setattr(identification, "enrichment_cache", lambda: cache)

    result = identification.identify(
        str(path),
        acoustid_key="key",
        acoustid_lookup=lambda _path, _key: {
            "artist": "ARXMANE & Crazy Mano",
            "title": "Matador De Dragão",
            "recording_id": "source-recording",
            "score": 0.98,
        },
    )

    assert result.exact_recording_id == ""
    assert result.derived_from_recording_id == "source-recording"
    assert result.is_derivative
