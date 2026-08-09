# pylint: disable=import-error,protected-access

from renamer import acoustid
from renamer import track_extraction as extractor
from renamer.filename_builder import build_filename
from renamer.track_extraction import TrackInfo, extract_track


def test_successful_acoustid_match_precedes_readable_tags(tmp_path, monkeypatch):
    path = tmp_path / "Tagged Artist - Tagged Title.mp3"
    path.write_bytes(b"audio")
    calls = []

    def fake_acoustid(candidate, extension, key):
        calls.append(("acoustid", candidate, extension, key))
        return TrackInfo(
            path=candidate,
            ext=extension,
            artist="AcoustID Artist",
            title="AcoustID Title",
            strategy="acoustid",
        )

    def fake_tags(_path):
        calls.append(("tags",))
        return {"TPE1": "Tagged Artist", "TIT2": "Tagged Title"}

    monkeypatch.setattr(extractor, "_from_acoustid", fake_acoustid)
    monkeypatch.setattr(extractor, "_read_tags", fake_tags)

    result = extract_track(str(path), acoustid_key="test-key")

    assert result.strategy == "acoustid"
    assert result.artist == "AcoustID Artist"
    assert result.title == "AcoustID Title"
    assert calls == [("acoustid", str(path), ".mp3", "test-key")]


def test_acoustid_ocremix_match_keeps_game_first_and_credits_remixer(tmp_path, monkeypatch):
    path = tmp_path / "Final Fantasy IV - GroundUp (OC ReMix).mp3"
    path.write_bytes(b"audio")
    monkeypatch.setattr(
        acoustid,
        "lookup",
        lambda *_args: {
            "artist": "FFmusic Dj",
            "title": "Final Fantasy IV Ground Up OC ReMix",
            "feat_artists": [],
            "score": 0.99,
            "recording_id": "recording",
        },
    )

    result = extract_track(str(path), acoustid_key="test-key")

    assert result.is_ocremix
    assert result.game == "Final Fantasy IV"
    assert result.title == "GroundUp"
    assert result.remixers == ("FFmusic Dj",)
    assert build_filename(result) == "Final Fantasy IV - GroundUp (FFmusic Dj) [OC ReMix].mp3"


def test_missing_acoustid_match_falls_back_to_tags(tmp_path, monkeypatch):
    path = tmp_path / "Tagged Artist - Tagged Title.mp3"
    path.write_bytes(b"audio")

    monkeypatch.setattr(extractor, "_from_acoustid", lambda *_args: None)
    monkeypatch.setattr(
        extractor,
        "_read_tags",
        lambda _path: {"TPE1": "Tagged Artist", "TIT2": "Tagged Title"},
    )

    result = extract_track(str(path), acoustid_key="test-key")

    assert result.strategy == "tag_based"
    assert result.artist == "Tagged Artist"
    assert result.title == "Tagged Title"


def test_missing_acoustid_key_skips_lookup_and_uses_tags(tmp_path, monkeypatch):
    path = tmp_path / "Tagged Artist - Tagged Title.mp3"
    path.write_bytes(b"audio")

    def unexpected_acoustid(*_args):
        raise AssertionError("AcoustID should not run without an API key")

    monkeypatch.setattr(extractor, "_from_acoustid", unexpected_acoustid)
    monkeypatch.setattr(
        extractor,
        "_read_tags",
        lambda _path: {"TPE1": "Tagged Artist", "TIT2": "Tagged Title"},
    )

    result = extract_track(str(path))

    assert result.strategy == "tag_based"
    assert result.artist == "Tagged Artist"
    assert result.title == "Tagged Title"


def test_square_bracket_ocremix_filename_keeps_game_first(tmp_path, monkeypatch):
    path = tmp_path / "Aeroz - The 7th Guest [OC ReMix].mp3"
    monkeypatch.setattr(extractor, "_read_tags", lambda _path: {})

    result = extract_track(str(path))

    assert result.is_ocremix
    assert result.game == "Aeroz"
    assert result.title == "The 7th Guest"
    assert build_filename(result) == "Aeroz - The 7th Guest [OC ReMix].mp3"


def test_parenthetical_ocremix_filename_keeps_game_and_remixer(tmp_path, monkeypatch):
    path = tmp_path / "Aeroz - The 7th Guest (Chernobague) (OC ReMix).mp3"
    monkeypatch.setattr(extractor, "_read_tags", lambda _path: {})

    result = extract_track(str(path))

    assert result.is_ocremix
    assert result.game == "Aeroz"
    assert result.title == "The 7th Guest"
    assert result.remixers == ("Chernobague",)
    assert build_filename(result) == "Aeroz - The 7th Guest (Chernobague) [OC ReMix].mp3"


def test_explicit_filename_strategy_still_overrides_acoustid(tmp_path, monkeypatch):
    path = tmp_path / "Filename Artist - Filename Title.mp3"
    path.write_bytes(b"audio")

    def unexpected_acoustid(*_args):
        raise AssertionError("AcoustID should not run for an explicit strategy")

    monkeypatch.setattr(extractor, "_from_acoustid", unexpected_acoustid)

    result = extract_track(
        str(path),
        strategy="regular",
        acoustid_key="test-key",
    )

    assert result.strategy == "filename_norm"
    assert result.artist == "Filename Artist"
    assert result.title == "Filename Title"


def test_acoustid_preserves_filename_instrumental_despite_identity_difference(tmp_path):
    path = tmp_path / "2Pac - Pac's Life (Instrumnetal).mp3"
    track = TrackInfo(
        path=str(path),
        ext=".mp3",
        artist="T.I.",
        title="A Different Recording",
        strategy="acoustid",
    )

    result = extractor._preserve_filename_version_qualifiers(str(path), track)

    assert result.title == "A Different Recording (Instrumental)"
    assert result.version_warning == ""


def test_acoustid_version_conflict_requires_review(tmp_path):
    path = tmp_path / "Artist - Song (Instrumental).mp3"
    track = TrackInfo(
        path=str(path),
        ext=".mp3",
        artist="Artist",
        title="Song (Radio Edit)",
        strategy="acoustid",
    )

    result = extractor._preserve_filename_version_qualifiers(str(path), track)

    assert result.title == "Song (Radio Edit) (Instrumental)"
    assert result.version_warning.startswith("Version qualifier conflicts with AcoustID metadata;")


def test_acoustid_preserves_unparenthesized_instrumental_label(tmp_path):
    path = tmp_path / "Artist - Song Instrumental (Bonus Track).mp3"
    track = TrackInfo(
        path=str(path),
        ext=".mp3",
        artist="Artist",
        title="Song",
        strategy="acoustid",
    )

    result = extractor._preserve_filename_version_qualifiers(str(path), track)

    assert result.title == "Song (Instrumental)"
