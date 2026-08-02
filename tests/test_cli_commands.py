# pylint: disable=import-error

from argparse import Namespace
from types import SimpleNamespace

from cli.commands import enrich, rename, shared
from cli.config import FolderConfig


class _Output:
    def __init__(self, approved=True):
        self.messages = []
        self.approved = approved

    def print(self, message=""):
        self.messages.append(message)

    def confirm(self, _prompt):
        return self.approved


def test_online_key_is_resolved_without_rendering_credential(monkeypatch):
    output = _Output()
    monkeypatch.setattr(shared, "resolve_acoustid_key", lambda: "private-test-key")
    monkeypatch.setattr(shared, "resolve_fpcalc", lambda: "fpcalc.exe")

    assert shared.online_key(True, output) == "private-test-key"
    assert all("private-test-key" not in message for message in output.messages)


def test_rename_dry_run_uses_review_api_without_applying(tmp_path, monkeypatch):
    source = tmp_path / "Old.mp3"
    source.write_bytes(b"audio")
    proposal = SimpleNamespace(
        id="rename-1",
        old_path=str(source),
        new_path=str(tmp_path / "Artist - Title.mp3"),
        apply_eligible=True,
        requires_review=False,
    )
    monkeypatch.setattr(
        rename,
        "command_folders",
        lambda *_args, **_kwargs: [FolderConfig(str(tmp_path), recursive=True)],
    )
    monkeypatch.setattr(rename, "online_key", lambda *_args: None)
    monkeypatch.setattr(rename, "scan_folder", lambda *_args, **_kwargs: [str(source)])
    monkeypatch.setattr(
        rename,
        "plan_renames",
        lambda **_kwargs: ([proposal], []),
    )
    monkeypatch.setattr(
        rename,
        "apply_review_plan",
        lambda *_args: (_ for _ in ()).throw(AssertionError("apply called")),
    )
    args = Namespace(
        folder=str(tmp_path),
        config=None,
        strategy=None,
        lookup=False,
        fingerprint=False,
        apply=False,
        interactive=False,
    )
    output = _Output()

    assert rename.run(args, output) == 0
    assert any("1 of 1 files would be renamed" in message for message in output.messages)


def test_enrich_passes_explicit_cover_art_preference(tmp_path, monkeypatch):
    observed = {}
    monkeypatch.setattr(
        enrich,
        "command_folders",
        lambda *_args, **_kwargs: [FolderConfig(str(tmp_path), recursive=True)],
    )
    monkeypatch.setattr(enrich, "online_key", lambda *_args: None)
    monkeypatch.setattr(
        enrich,
        "analyze_folder",
        lambda *_args, **kwargs: observed.update(kwargs)
        or SimpleNamespace(rename_proposals=(), tag_proposals=(), issues=()),
    )
    args = Namespace(
        folder=str(tmp_path),
        config=None,
        fingerprint=False,
        apply=False,
        cover_art=False,
    )

    assert enrich.run(args, _Output()) == 0
    assert observed["enrich_metadata"] is True
    assert observed["include_artwork"] is False
