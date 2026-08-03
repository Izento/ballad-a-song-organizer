# pylint: disable=import-error,protected-access

from argparse import Namespace
from types import SimpleNamespace

import pytest

from cli import app
from cli.commands import enrich, rename, shared, undo
from cli.config import FolderConfig, load_config, resolve_folders
from cli.parser import parse_args


def test_no_arguments_launch_gui(monkeypatch, cli_output):
    monkeypatch.setattr(app, "_configure_utf8_console", lambda: None)
    monkeypatch.setattr(app, "_load_local_environment", lambda: None)
    monkeypatch.setattr(app, "ensure_app_dirs", lambda: None)
    monkeypatch.setattr(app, "_run_gui", lambda: 7)

    assert app.main([], output=cli_output()) == 7


def test_dispatch_returns_command_status(monkeypatch, cli_output):
    monkeypatch.setattr(undo, "run", lambda _args, _output: 3)

    assert app._dispatch("undo", Namespace(), cli_output()) == 3


def test_online_key_is_resolved_without_rendering_credential(monkeypatch, cli_output):
    output = cli_output()
    monkeypatch.setattr(shared, "resolve_acoustid_key", lambda: "private-test-key")
    monkeypatch.setattr(shared, "resolve_fpcalc", lambda: "fpcalc.exe")

    assert shared.online_key(True, output) == "private-test-key"
    assert all("private-test-key" not in message for message in output.messages)


def test_rename_dry_run_uses_review_api_without_applying(
    tmp_path,
    monkeypatch,
    cli_output,
):
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
    output = cli_output()

    assert rename.run(args, output) == 0
    assert any("1 of 1 files would be renamed" in message for message in output.messages)


def test_enrich_passes_explicit_cover_art_preference(tmp_path, monkeypatch, cli_output):
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
        lambda *_args, **kwargs: (
            observed.update(kwargs)
            or SimpleNamespace(rename_proposals=(), tag_proposals=(), issues=())
        ),
    )
    args = Namespace(
        folder=str(tmp_path),
        config=None,
        fingerprint=False,
        apply=False,
        cover_art=False,
    )

    assert enrich.run(args, cli_output()) == 0
    assert observed["enrich_metadata"] is True
    assert observed["include_artwork"] is False


def test_missing_local_config_is_an_empty_batch(tmp_path):
    assert load_config(tmp_path / "missing.yaml") == []


def test_load_config_returns_validated_folder_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
folders:
  - path: D:\\Music
    strategy: regular
    recursive: false
    lookup: true
""".strip(),
        encoding="utf-8",
    )

    assert load_config(path) == [
        FolderConfig(
            path="D:\\Music",
            strategy="regular",
            recursive=False,
            lookup=True,
        )
    ]


def test_invalid_folder_entry_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("folders:\n  - recursive: true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty path"):
        load_config(path)


def test_explicit_folder_overrides_local_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("folders:\n  - path: ignored\n", encoding="utf-8")

    assert resolve_folders(
        "D:\\Selected",
        str(config),
        strategy="filename_norm",
        lookup=True,
    ) == [
        FolderConfig(
            path="D:\\Selected",
            strategy="filename_norm",
            recursive=True,
            lookup=True,
        )
    ]


def test_no_command_defaults_to_gui_dispatch():
    assert parse_args([]).command is None


def test_rename_options_are_scoped_to_rename_command():
    args = parse_args(
        [
            "rename",
            "--folder",
            "D:\\Music",
            "--strategy",
            "regular",
            "--fingerprint",
            "--apply",
        ]
    )

    assert args.command == "rename"
    assert args.folder == "D:\\Music"
    assert args.strategy == "regular"
    assert args.fingerprint is True
    assert args.apply is True


def test_enrich_defaults_to_cover_art_and_can_skip_it():
    assert parse_args(["enrich"]).cover_art is True
    assert parse_args(["enrich", "--no-cover-art"]).cover_art is False


@pytest.mark.parametrize(
    "legacy_option",
    [
        "--audit",
        "--sync-tags",
        "--dedup-regular",
        "--dedup-ocremix",
        "--undo",
        "--acoustid-key",
    ],
)
def test_legacy_action_flags_are_rejected(legacy_option):
    with pytest.raises(SystemExit):
        parse_args([legacy_option])
