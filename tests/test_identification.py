# pylint: disable=import-error

from renamer.cache import EnrichmentCache
from renamer import identification


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
