# pylint: disable=import-error,protected-access

from mutagen.id3 import ID3

from renamer import tag_writer


def test_mp3_writer_embeds_rich_tags_and_front_art(tmp_path):
    path = tmp_path / "track.mp3"
    path.write_bytes(b"")
    artwork = tmp_path / "cover.jpg"
    artwork.write_bytes(b"\xff\xd8\xffcover")

    tag_writer._write_mp3(
        str(path),
        {
            "artist": "Artist",
            "title": "Title",
            "tracknumber": "2",
            "genre": ["Electronic", "House"],
            "musicbrainz_recordingid": "recording-id",
        },
        {
            "path": str(artwork),
            "mime_type": "image/jpeg",
        },
    )

    tags = ID3(path)
    assert tags["TPE1"].text == ["Artist"]
    assert tags["TRCK"].text == ["2"]
    # ID3v2.3 serializes multi-value genres with a slash separator.
    assert tags["TCON"].text == ["Electronic/House"]
    assert tags["TXXX:MUSICBRAINZ_RECORDINGID"].text == ["recording-id"]
    assert tags.getall("APIC")[0].data == b"\xff\xd8\xffcover"
