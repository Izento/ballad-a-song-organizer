# pylint: disable=import-error

from renamer.cache import EnrichmentCache


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
