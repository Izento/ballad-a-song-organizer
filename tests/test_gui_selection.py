# pylint: disable=import-error,protected-access

from types import SimpleNamespace
from typing import Any

from gui import app as gui_app
from gui.app import SongOrganizerApp
from gui.controllers import actions as action_controller
from gui.controllers import context_menu as context_menu_controller
from gui.presentation import plan_rows, tag_display
from gui.session import ReviewSession
from gui.theme import _SHIFT_MASK
from renamer.domain.issues import ReviewIssue
from renamer.proposal_selection import requires_review
from renamer.review_models import (
    DuplicateFinding,
    FileSnapshot,
    RenameProposal,
    ReviewPlan,
    TagProposal,
)


def test_version_qualifier_conflict_requires_manual_review():
    issue = ReviewIssue.from_message(
        "Version qualifier conflicts with AcoustID metadata; review the proposed filename."
    )
    proposal = SimpleNamespace(requires_review=issue.requires_review)

    assert requires_review(proposal)


def _bare_app(plan=None) -> Any:
    app: Any = SongOrganizerApp.__new__(SongOrganizerApp)
    app.session = ReviewSession(plan=plan)
    return app


def test_browse_resets_previous_review_state(tmp_path, monkeypatch, fake_status):
    selected_folder = tmp_path / "new-library"
    selected_folder.mkdir()
    plan = ReviewPlan.create(str(tmp_path), False)
    app = _bare_app(plan)
    app.session.selected_ids.add("old-selection")
    app.session.applied_group_ids.add("old-group")
    app.session.recovery_overrides.add(str(tmp_path))
    app.folder_var = SimpleNamespace(set=lambda value: setattr(app, "selected_folder", value))
    app.status_var = fake_status()
    cleared = []
    app._clear_trees = lambda: cleared.append("trees")
    app._clear_activity_log = lambda: cleared.append("activity")
    app._update_review_details = lambda proposal: cleared.append(("details", proposal))
    app._update_primary_button = lambda: cleared.append("primary")
    monkeypatch.setattr(
        action_controller.filedialog,
        "askdirectory",
        lambda title: str(selected_folder),
    )

    app._browse()

    assert app.selected_folder == str(selected_folder)
    assert app.session.plan is None
    assert app.session.selected_ids == set()
    assert app.session.applied_group_ids == set()
    assert app.session.recovery_overrides == set()
    assert cleared == ["trees", "activity", ("details", None), "primary"]
    assert app.status_var.value == "Folder selected. Click Organize library to analyze."


def test_select_all_adds_every_ready_component_in_a_song_group(
    tmp_path,
    fake_tree,
    fake_status,
):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Old"},
        after={"artist": "New"},
        confidence="high",
        reason="test",
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[rename],
            tag_proposals=[tag],
        )
    )
    app.session.row_group_ids = {("changes", "shared-row"): "group-1"}
    app.trees = {
        "changes": fake_tree({"shared-row": ("☐", "", "", "", "")}),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    app._select_all()

    assert app.session.selected_ids == {rename.id, tag.id}
    assert app.trees["changes"].rows["shared-row"][0] == "☑"


def test_bulk_selection_accumulates_and_clear_selection_removes_all(
    tmp_path,
    fake_tree,
    fake_status,
):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="rename-group",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "renamed.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "renamed.mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="tag-group",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Old"},
        after={"artist": "New"},
        confidence="high",
        reason="test",
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[rename],
            tag_proposals=[tag],
        )
    )
    app.session.row_group_ids = {
        ("changes", "rename-row"): "rename-group",
        ("changes", "tag-row"): "tag-group",
    }
    app.trees = {
        "changes": fake_tree(
            {
                "rename-row": ("☐", "", "", "", ""),
                "tag-row": ("☐", "", "", "", ""),
            }
        ),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    app._set_selected_ids({rename.id}, expand_groups=False)
    app._select_all()

    assert app.session.selected_ids == {rename.id, tag.id}
    assert all(values[0] == "☑" for values in app.trees["changes"].rows.values())

    app._clear_selection()

    assert app.session.selected_ids == set()
    assert all(values[0] == "☐" for values in app.trees["changes"].rows.values())


def test_component_toggle_can_leave_a_partial_song_selection(
    tmp_path,
    fake_tree,
):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "renamed.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "renamed.mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Old"},
        after={"artist": "New"},
        confidence="high",
        reason="test",
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[rename],
            tag_proposals=[tag],
        )
    )
    app.session.row_group_ids = {("changes", "song-row"): "group-1"}
    app.trees = {
        "changes": fake_tree({"song-row": ("☐", "", "", "", "")}),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app._set_selected_ids({rename.id, tag.id}, expand_groups=False)

    app._toggle_component_selection(tag.id, False)

    assert app.session.selected_ids == {rename.id}
    assert app.trees["changes"].rows["song-row"][0] == "◩"


def test_checkbox_selects_the_entire_song_group(tmp_path, fake_tree, fake_status):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Old"},
        after={"artist": "New"},
        confidence="high",
        reason="test",
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[rename],
            tag_proposals=[tag],
        )
    )
    app.session.row_group_ids = {("changes", "song-row"): "group-1"}
    app.trees = {
        "changes": fake_tree({"song-row": ("☐", "", "", "", "")}),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    app._handle_tree_click("changes", SimpleNamespace(x=5, y="song-row"))

    assert app.session.selected_ids == {rename.id, tag.id}
    assert app.trees["changes"].rows["song-row"][0] == "☑"


def test_checkbox_can_select_applyable_review_item(tmp_path, fake_tree, fake_status):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    review = RenameProposal(
        id="rename-review",
        decision_group_id="review",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="medium",
        reason="review this match",
        warnings=(
            "Version qualifier conflicts with AcoustID metadata; review the proposed filename.",
        ),
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[review],
        )
    )
    app.session.row_group_ids = {("changes", "review-row"): "review"}
    app.trees = {
        "changes": fake_tree({"review-row": ("☐", "", "", "", "")}),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    result = app._handle_tree_click(
        "changes",
        SimpleNamespace(x=5, y="review-row"),
    )

    assert result == "break"
    assert review.requires_review
    assert review.apply_eligible
    assert app.session.selected_ids == {review.id}
    assert app.trees["changes"].rows["review-row"][0] == "☑"


def test_select_all_ready_skips_destination_collisions(tmp_path, fake_tree, fake_status):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    safe = RenameProposal(
        id="rename-safe",
        decision_group_id="safe",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "safe.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "safe.mp3"},
        confidence="high",
        reason="test",
    )
    review = RenameProposal(
        id="rename-review",
        decision_group_id="review",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "collision.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "collision.mp3"},
        confidence="high",
        reason="test",
        warnings=("Destination already exists: collision.mp3",),
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[safe, review],
        )
    )
    app.session.row_group_ids = {
        ("changes", "safe-row"): "safe",
        ("changes", "review-row"): "review",
    }
    app.trees = {
        "changes": fake_tree(
            {
                "safe-row": ("☐", "", "", "", ""),
                "review-row": ("☐", "", "", "", ""),
            }
        ),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    app._select_all()

    assert app.session.selected_ids == {safe.id}
    assert app.trees["changes"].rows["safe-row"][0] == "☑"
    assert app.trees["changes"].rows["review-row"][0] == "☐"


def test_checkbox_toggles_one_song_group_on_and_off(tmp_path, fake_tree, fake_status):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"audio")
    proposal = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=FileSnapshot.capture(str(source)),
        old_path=str(source),
        new_path=str(tmp_path / "renamed.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "renamed.mp3"},
        confidence="high",
        reason="test",
    )
    app = _bare_app(ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal]))
    app.session.row_group_ids = {("changes", "row-1"): "group-1"}
    app.trees = {
        "changes": fake_tree({"row-1": ("☐", "", "", "", "")}),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    result = app._handle_tree_click(
        "changes",
        SimpleNamespace(x=5, y="row-1"),
    )

    assert result == "break"
    assert app.session.selected_ids == {proposal.id}
    assert app.trees["changes"].rows["row-1"][0] == "☑"

    app._handle_tree_click("changes", SimpleNamespace(x=5, y="row-1"))

    assert app.session.selected_ids == set()
    assert app.trees["changes"].rows["row-1"][0] == "☐"


def test_shift_clicking_checkbox_selects_the_range(tmp_path, fake_tree, fake_status):
    proposals = []
    rows = {}
    row_group_ids = {}
    for index in range(1, 4):
        source = tmp_path / f"old-{index}.mp3"
        source.write_bytes(b"audio")
        proposal = RenameProposal(
            id=f"rename-{index}",
            decision_group_id=f"group-{index}",
            snapshot=FileSnapshot.capture(str(source)),
            old_path=str(source),
            new_path=str(tmp_path / f"new-{index}.mp3"),
            current_values={"filename": source.name},
            proposed_values={"filename": f"new-{index}.mp3"},
            confidence="high",
            reason="test",
        )
        proposals.append(proposal)
        rows[f"row-{index}"] = ("☐", "", "", "", "")
        row_group_ids[("changes", f"row-{index}")] = proposal.decision_group_id

    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=proposals,
        )
    )
    app.session.row_group_ids = row_group_ids
    app.session.selection_anchors = {"changes": "row-1"}
    app.trees = {
        "changes": fake_tree(rows, selected=("row-1",)),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    result = app._handle_tree_click(
        "changes",
        SimpleNamespace(x=5, y="row-3", state=_SHIFT_MASK),
    )

    assert result == "break"
    assert app.trees["changes"].selected == (
        "row-1",
        "row-2",
        "row-3",
    )
    assert app.session.selected_ids == {
        "rename-1",
        "rename-2",
        "rename-3",
    }


def test_right_click_file_opens_context_menu_for_exact_path(monkeypatch, fake_tree):
    app = _bare_app()
    app.root = object()
    app.session.row_paths = {
        ("changes", "row-1"): r"F:\Music\Hip-Hop\Artist - Song.mp3",
    }
    app.trees = {
        "changes": fake_tree({"row-1": ("☐",)}, selected=()),
    }
    opened = []
    app._open_in_file_explorer = opened.append
    menus = []

    class _FakeMenu:
        def __init__(self, *_args, **_kwargs):
            self.command = None
            menus.append(self)

        def add_command(self, *, command, **_kwargs):
            self.command = command

        def tk_popup(self, x, y):
            assert (x, y) == (40, 50)
            assert self.command is not None
            self.command()

    monkeypatch.setattr(gui_app.tk, "Menu", _FakeMenu)

    result = app._handle_tree_context_menu(
        "changes",
        SimpleNamespace(x_root=40, y_root=50, y="row-1"),
    )

    assert result == "break"
    assert app.trees["changes"].selection() == ("row-1",)
    assert opened == [r"F:\Music\Hip-Hop\Artist - Song.mp3"]
    assert len(menus) == 1


def test_open_file_explorer_passes_target_as_separate_argument(tmp_path, monkeypatch):
    path = tmp_path / "Artist - Song.mp3"
    path.write_bytes(b"audio")
    app = _bare_app()
    calls = []

    def fake_popen(command, **options):
        calls.append((command, options))

    monkeypatch.setattr(context_menu_controller.subprocess, "Popen", fake_popen)

    app._open_in_file_explorer(str(path))

    assert calls
    assert calls[0][0] == ["explorer.exe", "/select,", str(path)]


def test_tag_display_uses_compact_artist_title_values():
    assert tag_display({"artist": "Artist", "title": "Song"}) == "Artist / Song"
    assert tag_display({"title": "Song"}) == "Song"


def test_shared_artwork_guard_removes_only_player_fallbacks(
    tmp_path,
    monkeypatch,
):
    folder_art = tmp_path / "folder.jpg"
    generated_art = tmp_path / "AlbumArt_deadbeef_Large.jpg"
    unrelated_art = tmp_path / "concert-poster.jpg"
    for path in (folder_art, generated_art, unrelated_art):
        path.write_bytes(b"image")
    monkeypatch.setattr(gui_app.messagebox, "askyesnocancel", lambda *_args, **_kwargs: True)
    app = _bare_app()

    removed = app._resolve_shared_folder_artwork(str(tmp_path))

    assert removed == (generated_art, folder_art)
    assert not folder_art.exists()
    assert not generated_art.exists()
    assert unrelated_art.exists()


def test_shared_artwork_dialog_caps_the_filename_preview(tmp_path, monkeypatch):
    artwork = [tmp_path / f"AlbumArt_{index:02d}_Large.jpg" for index in range(12)]
    for path in artwork:
        path.write_bytes(b"image")
    prompts = []

    def decline_removal(_title, message):
        prompts.append(message)
        return False

    monkeypatch.setattr(gui_app.messagebox, "askyesnocancel", decline_removal)
    app = _bare_app()

    removed = app._resolve_shared_folder_artwork(str(tmp_path))

    assert removed == ()
    assert "found 12 artwork file(s)" in prompts[0]
    assert "…and 4 more" in prompts[0]
    assert artwork[7].name in prompts[0]
    assert artwork[8].name not in prompts[0]


def test_cover_art_proposal_is_visible_in_metadata_review(tmp_path):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    proposal = TagProposal(
        id="tag-artwork",
        decision_group_id="group-artwork",
        snapshot=FileSnapshot.capture(str(source)),
        path=str(source),
        before={"artist": "Artist", "title": "Song"},
        after={"artist": "Artist", "title": "Song", "album": "The Album"},
        confidence="medium",
        reason="verified release",
        artwork_after={
            "path": str(tmp_path / "cover.jpg"),
            "sha256": "abc",
            "size": 3,
            "mime_type": "image/jpeg",
            "release_id": "release",
        },
    )
    plan = ReviewPlan.create(
        str(tmp_path),
        False,
        tag_proposals=[proposal],
    )

    row = plan_rows(plan)[0]

    assert row.action == "Planned changes"
    assert "Album: The Album" in row.metadata
    assert "Cover art" in row.metadata


def test_plan_rows_group_filename_and_metadata_changes_by_song(tmp_path):
    source = tmp_path / "Artist - Old Song.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "Artist - New Song.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "Artist - New Song.mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Artist", "title": "Old Song"},
        after={"artist": "Artist", "title": "New Song"},
        confidence="high",
        reason="test",
    )

    rows = plan_rows(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[rename],
            tag_proposals=[tag],
        )
    )

    assert len(rows) == 1
    assert rows[0].tree == "changes"
    assert rows[0].group_id == "group-1"
    assert rows[0].proposal_ids == ("rename-1", "tag-1")
    assert rows[0].filename == "Artist - New Song.mp3"
    assert rows[0].metadata == "Title: New Song"
    assert rows[0].status == "ready"


def test_select_missing_artwork_includes_medium_confidence_proposals(
    tmp_path,
    fake_tree,
    fake_status,
):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    proposal = TagProposal(
        id="tag-artwork",
        decision_group_id="group-artwork",
        snapshot=FileSnapshot.capture(str(source)),
        path=str(source),
        before={"artist": "Artist", "title": "Song"},
        after={"artist": "Artist", "title": "Song", "album": "The Album"},
        confidence="medium",
        reason="verified release",
        artwork_after={
            "path": str(tmp_path / "cover.jpg"),
            "sha256": "abc",
            "size": 3,
            "mime_type": "image/jpeg",
            "release_id": "release",
        },
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            tag_proposals=[proposal],
        )
    )
    app.session.row_group_ids = {("changes", "artwork-row"): "group-artwork"}
    changes = fake_tree({"artwork-row": ("☐", "", "", "", "")})
    changes.master = "changes-frame"
    app.trees = {
        "changes": changes,
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.tabs = {"changes": "changes-frame"}
    selected_tabs = []
    app.notebook = SimpleNamespace(select=selected_tabs.append)
    app.status_var = fake_status()

    app._select_artwork()

    assert app.session.selected_ids == {proposal.id}
    assert changes.rows["artwork-row"][0] == "☑"
    assert selected_tabs == ["changes-frame"]
    assert app.status_var.value == "Selected 1 verified cover-art change(s)."


def test_populate_plan_renders_each_duplicate_path(tmp_path, fake_tree):
    app = _bare_app()
    app.trees = {
        "changes": fake_tree({}),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    finding = DuplicateFinding(
        id="duplicate-1",
        paths=("first.mp3", "second.mp3"),
        classification="unsafe",
        recommendation="Keep both unless you confirm they are equivalent.",
        evidence={},
        confidence="low",
    )

    app._populate_plan(
        ReviewPlan.create(
            str(tmp_path),
            False,
            duplicate_findings=[finding],
        )
    )

    rows = app.trees["duplicates"].rows
    assert [values[1] for values in rows.values()] == ["first.mp3", "second.mp3"]
    assert [values[0] for values in rows.values()] == ["☐", "☐"]
    assert set(app.session.duplicate_row_ids.values()) == {
        ("duplicate-1", "first.mp3"),
        ("duplicate-1", "second.mp3"),
    }


def test_duplicate_selection_keeps_one_file_and_stays_out_of_apply_ids(
    tmp_path,
    fake_tree,
    fake_status,
):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    finding = DuplicateFinding(
        id="duplicate-1",
        paths=(str(first), str(second)),
        classification="auto-safe",
        recommendation="Keep one copy.",
        evidence={
            "hashes": {
                str(first): "hash",
                str(second): "hash",
            }
        },
        confidence="high",
    )
    app = _bare_app(ReviewPlan.create(str(tmp_path), False, duplicate_findings=[finding]))
    app.trees = {
        "changes": fake_tree({}),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()
    app._populate_plan(app.session.plan)

    app._handle_tree_click("duplicates", SimpleNamespace(x=5, y="row-0"))
    assert app.session.duplicate_selected_paths == {"duplicate-1": {str(first)}}
    assert app.session.selected_ids == set()

    app._handle_tree_click("duplicates", SimpleNamespace(x=5, y="row-1"))

    assert app.session.duplicate_selected_paths == {"duplicate-1": {str(first)}}
    assert "Keep at least one file" in app.status_var.value


def test_select_recommended_adds_complete_recommended_song_groups(
    tmp_path,
    fake_tree,
    fake_status,
):
    source = tmp_path / "Artist - Song (feat. Guest).mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "Artist - Song (feat. Guest).mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "Artist - Song (feat. Guest).mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"title": "Song"},
        after={"title": "Song (feat. Guest)"},
        confidence="high",
        reason="test",
    )
    low_confidence_rename = RenameProposal(
        id="rename-2",
        decision_group_id="group-2",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "other.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "other.mp3"},
        confidence="medium",
        reason="test",
    )
    unsafe_rename = RenameProposal(
        id="rename-3",
        decision_group_id="group-3",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "unsafe.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "unsafe.mp3"},
        confidence="high",
        reason="test",
        warnings=("Destination collides with another proposal.",),
    )
    plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[rename, low_confidence_rename, unsafe_rename],
        tag_proposals=[tag],
    )

    app = _bare_app(plan)
    app.session.row_group_ids = {
        ("changes", "group-1-row"): "group-1",
        ("changes", "group-2-row"): "group-2",
        ("changes", "group-3-row"): "group-3",
    }
    app.trees = {
        "changes": fake_tree(
            {
                "group-1-row": ("☐", "", "", "", ""),
                "group-2-row": ("☐", "", "", "", ""),
                "group-3-row": ("☐", "", "", "", ""),
            }
        ),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.status_var = fake_status()

    app._select_recommended()

    assert app.session.selected_ids == {rename.id, tag.id}
    assert app.trees["changes"].rows["group-1-row"][0] == "☑"
    assert app.trees["changes"].rows["group-2-row"][0] == "☐"
    assert app.trees["changes"].rows["group-3-row"][0] == "☐"


def test_edit_selected_filename_updates_plan_and_selection(
    tmp_path,
    monkeypatch,
    fake_tree,
    fake_status,
):
    source = tmp_path / "Artist - Wrong Spelling.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    proposal = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "Artist - Wrong Spelling.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "Artist - Wrong Spelling.mp3"},
        confidence="high",
        reason="test",
    )
    app = _bare_app(
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[proposal],
        )
    )
    app.session.selected_ids = {proposal.id}
    app.session.row_group_ids = {("changes", "rename-row"): "group-1"}
    app.session.row_paths = {}
    app.trees = {
        "changes": fake_tree(
            {"rename-row": ("☑", str(source), "old summary", "old tags", "ready")},
            selected=("rename-row",),
        ),
        "duplicates": fake_tree({}),
        "errors": fake_tree({}),
    }
    app.root = None
    app.status_var = fake_status()
    monkeypatch.setattr(
        action_controller,
        "_ask_filename",
        lambda *_args, **_kwargs: "Artist - Correct Spelling.mp3",
    )

    app._edit_selected_filename()

    updated = app.session.plan.rename_proposals[0]
    assert updated.proposed_values["filename"] == "Artist - Correct Spelling.mp3"
    assert updated.new_path.endswith("Artist - Correct Spelling.mp3")
    assert updated.id in app.session.selected_ids
    assert proposal.id not in app.session.selected_ids
    assert app.session.plan.validate_digest()
    assert any(
        values[2].endswith("Artist - Correct Spelling.mp3")
        for values in app.trees["changes"].rows.values()
    )


def test_progress_events_are_written_to_the_activity_log(
    fake_activity_log,
    fake_status,
):
    app = _bare_app()
    app.activity_log = fake_activity_log()
    app.status_var = fake_status()

    app._handle_event(
        (
            "progress",
            "Enrich metadata",
            2,
            7,
            r"C:\Music\Artist - Song.mp3",
        )
    )

    assert app.activity_log.entries == ["Enrich metadata: 2/7  C:\\Music\\Artist - Song.mp3\n"]
    assert app.activity_log.seen == ["end"]
    assert app.activity_log.states == ["normal", "disabled"]
    assert app.status_var.value == "Enrich metadata: 2/7  C:\\Music\\Artist - Song.mp3"


def test_activity_log_only_follows_tail_when_already_at_bottom(fake_activity_log):
    app = _bare_app()
    app.activity_log = fake_activity_log(view=(0.2, 0.5))

    app._append_activity_log("new entry")

    assert app.activity_log.seen == []

    app.activity_log.view = (0.5, 1.0)
    app._append_activity_log("tail entry")

    assert app.activity_log.seen == ["end"]


def test_recovery_override_requires_confirmation_once_per_folder(monkeypatch):
    app = _bare_app()
    messages = []
    app._append_activity_log = messages.append
    confirmations = []
    monkeypatch.setattr(
        gui_app.messagebox,
        "askyesno",
        lambda title, message: confirmations.append((title, message)) or True,
    )

    pending = [{"batch_id": "old-batch"}]
    assert app._confirm_recovery_override(pending, r"F:\Music\Tek No Logical")
    assert app._confirm_recovery_override(pending, r"F:\Music\Tek No Logical")

    assert len(confirmations) == 1
    assert messages == ["Continuing despite unresolved recovery for this folder."]
