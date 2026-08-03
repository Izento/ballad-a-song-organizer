import pytest

from renamer.dedup import analyze_regular_duplicates, collect_tracks
from renamer.review_models import path_key


def test_exact_content_duplicates_are_auto_safe(tmp_path):
    first = tmp_path / "Artist - Song.mp3"
    second = tmp_path / "Artist - Song copy.mp3"
    first.write_bytes(b"same audio bytes")
    second.write_bytes(b"same audio bytes")

    findings = analyze_regular_duplicates(str(tmp_path))

    assert len(findings) == 1
    assert findings[0].classification == "auto-safe"
    assert set(findings[0].paths) == {str(first), str(second)}


def test_versioned_files_are_not_grouped_as_safe_duplicates(tmp_path):
    first = tmp_path / "Artist - Song (Live).mp3"
    second = tmp_path / "Artist - Song (Acoustic).mp3"
    first.write_bytes(b"live recording")
    second.write_bytes(b"acoustic recording")

    findings = analyze_regular_duplicates(str(tmp_path))

    assert len(findings) == 1
    assert findings[0].classification == "unsafe"


def test_unparseable_filenames_are_not_grouped_without_identity_evidence(tmp_path):
    # Neither name fits "Artist - Title", and there are no tags to fall back
    # on, so there is no naming-derived identity to match on at all.
    first = tmp_path / "track01.mp3"
    second = tmp_path / "unknown_track_final_v2.mp3"
    first.write_bytes(b"first encode")
    second.write_bytes(b"second encode")

    findings = analyze_regular_duplicates(str(tmp_path))

    assert findings == []


def test_identity_override_groups_true_duplicates_despite_bad_filenames(tmp_path):
    # This is what a same-run metadata enrichment pass supplies: two files
    # named nothing alike, both verified as the same recording. Near-
    # duplicate matching should trust that over guessing from the filename.
    first = tmp_path / "track01.mp3"
    second = tmp_path / "unknown_track_final_v2.mp3"
    first.write_bytes(b"first encode")
    second.write_bytes(b"second encode")
    overrides = {
        path_key(str(first)): ("Real Artist", "Real Title"),
        path_key(str(second)): ("Real Artist", "Real Title"),
    }

    findings = analyze_regular_duplicates(
        str(tmp_path),
        identity_overrides=overrides,
    )

    assert len(findings) == 1
    assert set(findings[0].paths) == {str(first), str(second)}
    assert findings[0].classification in {"review", "unsafe"}


def test_pre_collected_tracks_can_be_grouped_separately(tmp_path):
    # This is the concurrency-enabling contract: collection (disk/CPU-bound)
    # can happen on a background thread with no identity overrides yet,
    # while grouping (cheap, in-memory) happens afterward once they exist.
    first = tmp_path / "track01.mp3"
    second = tmp_path / "unknown_track_final_v2.mp3"
    first.write_bytes(b"first encode")
    second.write_bytes(b"second encode")

    tracks = collect_tracks(str(tmp_path), recursive=False)
    assert all(track.identity_override is None for track in tracks)

    overrides = {
        path_key(str(first)): ("Real Artist", "Real Title"),
        path_key(str(second)): ("Real Artist", "Real Title"),
    }
    findings = analyze_regular_duplicates(tracks=tracks, identity_overrides=overrides)

    assert len(findings) == 1
    assert set(findings[0].paths) == {str(first), str(second)}


def test_grouping_without_tracks_or_folder_path_raises():
    with pytest.raises(ValueError, match="folder_path is required"):
        analyze_regular_duplicates()
