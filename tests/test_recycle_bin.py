import pytest

from renamer import recycle_bin
from renamer.recycle_bin import apply_selected_duplicates
from renamer.review_models import DuplicateFinding, sha256_file


def _finding(first, second):
    return DuplicateFinding(
        id="duplicate-1",
        paths=(str(first), str(second)),
        classification="auto-safe",
        recommendation="Keep one copy.",
        evidence={
            "hashes": {
                str(first): sha256_file(str(first)),
                str(second): sha256_file(str(second)),
            }
        },
        confidence="high",
    )


def test_selected_duplicate_moves_to_recycle_bin_and_logs_result(
    tmp_path,
    monkeypatch,
    app_paths,
):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    calls = []
    monkeypatch.setattr(recycle_bin, "send_to_recycle_bin", calls.append)
    monkeypatch.setattr(recycle_bin, "ensure_app_dirs", lambda: app_paths(tmp_path / "state"))

    results = apply_selected_duplicates(_finding(first, second), [str(first)])

    assert [(result.path, result.status) for result in results] == [(str(first), "succeeded")]
    assert calls == [str(first)]
    log = (tmp_path / "state" / "Logs" / "recycle-duplicate-1.json").read_text()
    assert '"status": "succeeded"' in log
    assert first.exists()
    assert second.exists()


def test_changed_duplicate_is_rejected_before_recycle_bin_call(
    tmp_path,
    monkeypatch,
    app_paths,
):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"original")
    second.write_bytes(b"same")
    finding = _finding(first, second)
    first.write_bytes(b"changed")
    calls = []
    monkeypatch.setattr(recycle_bin, "send_to_recycle_bin", calls.append)
    monkeypatch.setattr(recycle_bin, "ensure_app_dirs", lambda: app_paths(tmp_path / "state"))

    results = apply_selected_duplicates(finding, [str(first)])

    assert results[0].status == "failed"
    assert "changed since review" in results[0].message
    assert calls == []


def test_non_member_duplicate_path_is_rejected(tmp_path):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    outsider = tmp_path / "outsider.mp3"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    outsider.write_bytes(b"other")

    with pytest.raises(ValueError, match="not part"):
        apply_selected_duplicates(_finding(first, second), [str(outsider)])


def test_removing_every_file_in_duplicate_group_is_rejected(tmp_path):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    finding = _finding(first, second)

    with pytest.raises(ValueError, match="at least one"):
        apply_selected_duplicates(finding, [str(first), str(second)])
