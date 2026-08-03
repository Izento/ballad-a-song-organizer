from renamer.quarantine import (
    is_quarantined,
    load_quarantine,
    quarantine_file,
    unquarantine_files,
)
from renamer.review_models import path_key


def test_quarantine_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    test_file = tmp_path / "song.mp3"
    test_file.write_bytes(b"audio")

    assert not is_quarantined(str(test_file))

    record = quarantine_file(
        str(test_file),
        artist="Test Artist",
        title="Test Title",
        reason="Testing",
    )

    assert record["path_key"] == path_key(str(test_file))
    assert is_quarantined(str(test_file))

    items = load_quarantine()
    assert len(items) == 1
    assert items[0]["artist"] == "Test Artist"

    removed = unquarantine_files([path_key(str(test_file))])
    assert removed == 1
    assert not is_quarantined(str(test_file))
