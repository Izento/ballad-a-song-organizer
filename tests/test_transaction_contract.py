# pylint: disable=import-error

from dataclasses import replace
from pathlib import Path

from renamer import apply as apply_module
from renamer.review_models import (
    ApplyResult,
    FileSnapshot,
    RenameProposal,
    ReviewPlan,
    TagProposal,
)


def _app_paths(root: Path) -> dict[str, Path]:
    paths = {
        "root": root,
        "config": root / "config.yaml",
        "cache": root / "Cache",
        "backups": root / "Backups",
        "journals": root / "Journals",
        "logs": root / "Logs",
    }
    for key, path in paths.items():
        if key != "config":
            path.mkdir(parents=True, exist_ok=True)
    return paths


def _coordinated_plan(tmp_path: Path) -> tuple[ReviewPlan, TagProposal, RenameProposal]:
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(
        str(source),
        tags={"artist": "Old", "title": "Title"},
        include_hash=True,
    )
    tag = TagProposal(
        id="tag",
        decision_group_id="song",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Old", "title": "Title"},
        after={"artist": "New", "title": "Title"},
        confidence="high",
        reason="test",
    )
    rename = RenameProposal(
        id="rename",
        decision_group_id="song",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="high",
        reason="test",
    )
    return (
        ReviewPlan.create(
            str(tmp_path),
            False,
            rename_proposals=[rename],
            tag_proposals=[tag],
        ),
        tag,
        rename,
    )


def test_apply_rejects_invalid_plan_digest_before_mutation(tmp_path, monkeypatch):
    plan, _tag, rename = _coordinated_plan(tmp_path)
    monkeypatch.setattr(
        apply_module,
        "ensure_app_dirs",
        lambda: _app_paths(tmp_path / "state"),
    )

    results = apply_module.apply_review_plan(
        replace(plan, digest="tampered"),
        [rename.id],
    )

    assert results[0].status == "blocked"
    assert Path(rename.old_path).is_file()
    assert not Path(rename.new_path).exists()


def test_failed_tag_prevents_same_song_rename(tmp_path, monkeypatch):
    plan, tag, rename = _coordinated_plan(tmp_path)
    monkeypatch.setattr(
        apply_module,
        "ensure_app_dirs",
        lambda: _app_paths(tmp_path / "state"),
    )
    monkeypatch.setattr(
        apply_module,
        "_apply_tag",
        lambda item, _journal: ApplyResult(
            proposal_id=item.id,
            status="failed",
            path=item.path,
            message="injected failure",
        ),
    )

    results = apply_module.apply_review_plan(plan, [tag.id, rename.id])
    statuses = {result.proposal_id: result.status for result in results}

    assert statuses == {"tag": "failed", "rename": "blocked"}
    assert Path(rename.old_path).is_file()
    assert not Path(rename.new_path).exists()


def test_tag_failure_does_not_block_unrelated_song(tmp_path, monkeypatch):
    proposals = []
    for group in ("first", "second"):
        source = tmp_path / f"{group}-old.mp3"
        source.write_bytes(b"audio")
        snapshot = FileSnapshot.capture(
            str(source),
            tags={"artist": "Old", "title": group},
            include_hash=True,
        )
        proposals.append(
            (
                TagProposal(
                    id=f"{group}-tag",
                    decision_group_id=group,
                    snapshot=snapshot,
                    path=str(source),
                    before={"artist": "Old", "title": group},
                    after={"artist": "New", "title": group},
                    confidence="high",
                    reason="test",
                ),
                RenameProposal(
                    id=f"{group}-rename",
                    decision_group_id=group,
                    snapshot=snapshot,
                    old_path=str(source),
                    new_path=str(tmp_path / f"{group}-new.mp3"),
                    current_values={"filename": source.name},
                    proposed_values={"filename": f"{group}-new.mp3"},
                    confidence="high",
                    reason="test",
                ),
            )
        )
    tags = [pair[0] for pair in proposals]
    renames = [pair[1] for pair in proposals]
    plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=renames,
        tag_proposals=tags,
    )
    monkeypatch.setattr(
        apply_module,
        "ensure_app_dirs",
        lambda: _app_paths(tmp_path / "state"),
    )

    def fake_apply(item, _journal):
        status = "failed" if item.decision_group_id == "first" else "succeeded"
        return ApplyResult(item.id, status, item.path, "injected")

    monkeypatch.setattr(apply_module, "_apply_tag", fake_apply)

    results = apply_module.apply_review_plan(
        plan,
        [item.id for item in (*tags, *renames)],
    )
    statuses = {result.proposal_id: result.status for result in results}

    assert statuses["first-tag"] == "failed"
    assert statuses["first-rename"] == "blocked"
    assert statuses["second-tag"] == "succeeded"
    assert statuses["second-rename"] == "succeeded"
    assert Path(renames[0].old_path).is_file()
    assert Path(renames[1].new_path).is_file()
