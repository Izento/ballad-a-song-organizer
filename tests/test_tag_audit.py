# pylint: disable=import-error

from renamer.media import MediaRead
from renamer.media.legacy_filename import parse_stem, parsed_tag_values
from renamer.tag_audit import (
    audit_tag_file,
    audit_tags_for_folder,
    expected_tags_from_filename,
)


def test_filename_is_source_of_truth_for_regular_tags(tmp_path, monkeypatch):
    path = tmp_path / "Artist - Song (feat. Guest) (Remix).mp3"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(
        "renamer.tag_audit.read_media",
        lambda _path: MediaRead(
            path=str(path),
            status="ok",
            container="MP3",
            tags={"artist": "Wrong Artist", "title": "Wrong Title"},
        ),
    )

    proposal = audit_tag_file(str(path))

    assert proposal is not None
    assert proposal.before["artist"] == "Wrong Artist"
    assert proposal.after["artist"] == "Artist"
    assert proposal.after["title"] == "Song (Remix) (feat. Guest)"
    assert proposal.confidence == "high"


def test_ocremix_version_label_is_not_written_as_a_remixer():
    parsed = parse_stem("Game - Song Title (Radio Edit) [OC ReMix]")

    assert parsed is not None
    assert parsed["title"] == "Song Title (Radio Edit)"
    assert parsed["remixers"] == []


def test_regular_tag_writer_accepts_compact_hyphen_separator():
    parsed = parse_stem("Noisecontrollers-aliens")

    assert parsed is not None
    assert parsed["artist"] == "Noisecontrollers"
    assert parsed["full_title"] == "aliens"


def test_tag_writer_uses_canonical_regular_identity():
    parsed = parse_stem("Artist - Song ((feat. Guest)) [Extended Mix].mp3.mp3")

    assert parsed is not None
    assert parsed["full_title"] == "Song (Extended Mix) (feat. Guest)"


def test_tag_writer_canonicalizes_ocremix_parentheses_and_extension():
    parsed = parse_stem("Game - Song ((Beatdrop)) [OC ReMix].mp3.mp3")

    assert parsed is not None
    assert parsed["title"] == "Song"
    assert parsed["remixers"] == ["Beatdrop"]


def test_ocremix_parenthetical_marker_preserves_game_and_remixer():
    parsed = parse_stem("Game - Song (Beatdrop) (OC ReMix)")

    assert parsed is not None
    assert parsed["game"] == "Game"
    assert parsed["title"] == "Song"
    assert parsed["remixers"] == ["Beatdrop"]


def test_ocremix_filename_stores_creator_in_remixer_metadata():
    expected, _reason = expected_tags_from_filename(
        "Game - Song (Beatdrop) [OC ReMix].mp3",
        {},
    )

    assert expected["artist"] == "Game"
    assert expected["title"] == "Song (Beatdrop)"
    assert expected["remixer"] == ["Beatdrop"]
    assert expected["album_artist"] == "OverClocked ReMix"


def test_gamers_delight_ocremix_examples_keep_game_title_and_creator():
    cases = (
        (
            "Zelda II - The Adventure of Link - Temple Trippin' (LaRux) [OC ReMix]",
            "Zelda II",
            "The Adventure of Link - Temple Trippin'",
            "LaRux",
        ),
        (
            "Zombies Ate My Neighbors - Heart Beats (Mazedude) [OC ReMix]",
            "Zombies Ate My Neighbors",
            "Heart Beats",
            "Mazedude",
        ),
    )

    for stem, game, title, remixer in cases:
        parsed = parse_stem(stem)
        assert parsed is not None
        values = parsed_tag_values(parsed)
        assert values["artist"] == game
        assert values["title"] == f"{title} ({remixer})"
        assert values["remixer"] == [remixer]


def test_unwritable_tag_format_is_an_analysis_issue(tmp_path, monkeypatch):
    path = tmp_path / "Artist - Song.wav"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(
        "renamer.tag_audit.read_media",
        lambda _path: MediaRead(
            path=str(path),
            status="ok",
            container="WAVE",
            tags={"artist": "Old Artist", "title": "Old Song"},
        ),
    )

    proposals, issues = audit_tags_for_folder(str(tmp_path), recursive=False)

    assert proposals == []
    assert issues == [
        {
            "path": str(path.absolute()),
            "category": "tag-audit",
            "message": "Tag writing is not supported for .wav files",
        }
    ]
