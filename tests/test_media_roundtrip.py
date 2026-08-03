# pylint: disable=import-error

import shutil
from pathlib import Path

import pytest

from renamer.media import read_media, write_tags_to_file

FIXTURES = Path(__file__).parent / "fixtures"
FORMATS = ("mp3", "flac", "ogg", "m4a", "wma")
RICH_TAGS = {
    "artist": "Artist",
    "title": "Title",
    "album": "Album",
    "album_artist": "Album Artist",
    "tracknumber": "2",
    "tracktotal": "10",
    "discnumber": "1",
    "disctotal": "2",
    "date": "2024-04-05",
    "genre": ["Electronic", "House"],
    "composer": ["Composer One", "Composer Two"],
    "musicbrainz_recordingid": "recording-id",
    "musicbrainz_albumid": "release-id",
    "release_country": "US",
    "release_status": "Official",
    "release_type": "Album",
}


def _copy_fixture(tmp_path: Path, extension: str) -> Path:
    target = tmp_path / f"sample.{extension}"
    shutil.copy2(FIXTURES / f"sample.{extension}", target)
    return target


@pytest.mark.parametrize("extension", FORMATS)
def test_supported_container_round_trips_canonical_metadata(tmp_path, extension):
    target = _copy_fixture(tmp_path, extension)

    result = write_tags_to_file(str(target), RICH_TAGS)
    media = read_media(str(target))

    assert result == {"status": "updated"}
    assert media.usable
    for field in (
        "artist",
        "title",
        "album",
        "album_artist",
        "tracknumber",
        "tracktotal",
        "discnumber",
        "disctotal",
        "musicbrainz_recordingid",
        "musicbrainz_albumid",
        "release_country",
        "release_status",
        "release_type",
    ):
        assert media.tags[field] == str(RICH_TAGS[field])
    assert set(media.tags["genre"]) == {
        "Electronic",
        "House",
    }


def test_mp3_genre_containing_slash_round_trips_as_one_value(tmp_path):
    # MusicBrainz has real compound genre tags like "hip-hop/rap" -- if the
    # multi-value join separator were "/" (ID3v2.3's own default), this
    # single value would be indistinguishable on disk from two separate
    # genres joined together, and would keep reappearing as a "change" on
    # every re-run even though nothing about it changed.
    target = _copy_fixture(tmp_path, "mp3")

    write_tags_to_file(str(target), {"genre": ["hip-hop/rap", "pop"]})
    media = read_media(str(target))

    assert set(media.tags["genre"]) == {"hip-hop/rap", "pop"}


@pytest.mark.parametrize(
    ("extension", "cover_name"),
    (
        ("mp3", "cover.jpg"),
        ("flac", "cover.png"),
        ("ogg", "cover.png"),
        ("m4a", "cover.jpg"),
        ("wma", "cover.jpg"),
    ),
)
def test_supported_container_round_trips_front_artwork(
    tmp_path,
    extension,
    cover_name,
):
    target = _copy_fixture(tmp_path, extension)
    cover = FIXTURES / cover_name
    artwork = {
        "path": str(cover),
        "sha256": __import__("hashlib").sha256(cover.read_bytes()).hexdigest(),
        "size": cover.stat().st_size,
        "mime_type": "image/png" if cover.suffix == ".png" else "image/jpeg",
    }

    result = write_tags_to_file(str(target), {"artist": "Artist"}, artwork)
    media = read_media(str(target))

    assert result == {"status": "updated"}
    assert media.artwork is not None
    assert media.artwork["sha256"] == artwork["sha256"]
    assert media.artwork["size"] == artwork["size"]
