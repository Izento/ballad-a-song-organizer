# pylint: disable=import-error

import json
import hashlib
from pathlib import Path
import shutil

from renamer import apply as apply_module
from renamer.media import MediaRead
from renamer.media import read_media
from renamer.review_models import (
    ApplyResult,
    FileSnapshot,
    RenameProposal,
    ReviewPlan,
    TagProposal,
)
from renamer.tag_writer import write_tags_to_file


def _test_app_paths(root: Path):
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


def test_recovery_queries_can_be_scoped_to_review_root(tmp_path, monkeypatch):
    state = tmp_path / "state"
    paths = _test_app_paths(state)
    monkeypatch.setattr(apply_module, "ensure_app_dirs", lambda: paths)
    first_root = tmp_path / "first-music"
    second_root = tmp_path / "second-music"

    for batch_id, root in (("first", first_root), ("second", second_root)):
        (paths["journals"] / f"{batch_id}.json").write_text(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "root": str(root),
                    "status": "applying",
                    "actions": [{"status": "completed"}],
                }
            ),
            encoding="utf-8",
        )

    assert [
        batch["batch_id"]
        for batch in apply_module.batches_requiring_recovery(str(first_root))
    ] == ["first"]
    assert [
        batch["batch_id"]
        for batch in apply_module.batches_requiring_recovery(str(second_root))
    ] == ["second"]
    assert (
        apply_module.latest_undoable_batch(str(second_root))["batch_id"]
        == "second"
    )


def test_apply_uses_reviewed_rename_and_undo(tmp_path, monkeypatch):
    source = tmp_path / "old.mp3"
    destination = tmp_path / "new.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    snapshot = FileSnapshot.capture(str(source))
    proposal = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(destination),
        current_values={"filename": source.name},
        proposed_values={"filename": destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    assert destination.read_bytes() == b"audio"
    assert not source.exists()
    assert apply_module.latest_undoable_batch()["batch_id"] == plan.batch_id

    undo_results = apply_module.undo_batch(plan.batch_id)

    assert undo_results[0].status == "succeeded"
    assert source.read_bytes() == b"audio"
    assert apply_module.latest_undoable_batch() is None


def test_tag_apply_restores_backup(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    state = tmp_path / "state"
    monkeypatch.setattr(apply_module, "ensure_app_dirs", lambda: _test_app_paths(state))
    written = []
    monkeypatch.setattr(
        apply_module,
        "write_tags_to_file",
        lambda path, after: written.append((path, after)) or {"status": "updated"},
    )
    monkeypatch.setattr(
        apply_module,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Artist", "title": "Song"},
        ),
    )
    snapshot = FileSnapshot.capture(
        str(source), tags={"artist": "Wrong", "title": "Wrong"}
    )
    proposal = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Wrong", "title": "Wrong"},
        after={"artist": "Artist", "title": "Song"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    assert Path(results[0].backup_path).exists()
    assert written[0][0] != str(source)
    assert Path(written[0][0]).suffix == ".mp3"
    assert written[0][1] == {"artist": "Artist", "title": "Song"}
    backup = json.loads(Path(results[0].backup_path).read_text(encoding="utf-8"))
    assert backup["before"] == {"artist": "Wrong", "title": "Wrong"}


def test_apply_rejects_existing_unrelated_destination(tmp_path, monkeypatch):
    source = tmp_path / "old.mp3"
    destination = tmp_path / "new.mp3"
    source.write_bytes(b"source")
    destination.write_bytes(b"unrelated")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    proposal = RenameProposal(
        id="rename-collision",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        old_path=str(source),
        new_path=str(destination),
        current_values={"filename": source.name},
        proposed_values={"filename": destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "blocked"
    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"unrelated"


def test_apply_writes_reviewed_tags_for_nonstandard_filename(tmp_path, monkeypatch):
    source = tmp_path / "NoArtistTitle.mp3"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    monkeypatch.setattr(
        apply_module,
        "write_tags_to_file",
        lambda _path, _after: {"status": "updated"},
    )
    monkeypatch.setattr(
        apply_module,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "New", "title": "Title"},
        ),
    )
    proposal = TagProposal(
        id="tag-unsupported-name",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        path=str(source),
        before={"artist": "Old", "title": "Title"},
        after={"artist": "New", "title": "Title"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    assert source.read_bytes() == b"source"


def test_apply_preflights_unsupported_tag_file_type(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song.wav"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    proposal = TagProposal(
        id="tag-unsupported-type",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        path=str(source),
        before={"artist": "Old", "title": "Song"},
        after={"artist": "Artist", "title": "Song"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])

    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "blocked"
    assert "not supported for .wav files" in results[0].message
    assert source.read_bytes() == b"source"


def test_apply_continues_after_failed_tag_write(tmp_path, monkeypatch):
    failed_source = tmp_path / "Artist - Failed.mp3"
    safe_source = tmp_path / "Artist - Safe.mp3"
    failed_source.write_bytes(b"failed")
    safe_source.write_bytes(b"safe")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )

    def proposal(identifier: str, source: Path) -> TagProposal:
        return TagProposal(
            id=identifier,
            decision_group_id=identifier,
            snapshot=FileSnapshot.capture(str(source)),
            path=str(source),
            before={"artist": "Old", "title": source.stem},
            after={"artist": "Artist", "title": source.stem},
            confidence="high",
            reason="test",
        )

    failed = proposal("tag-failed", failed_source)
    safe = proposal("tag-safe", safe_source)
    calls = []

    def fake_apply_tag(item, _journal):
        calls.append(item.id)
        if item.id == failed.id:
            return ApplyResult(
                proposal_id=item.id,
                status="failed",
                path=item.path,
                message="Simulated tag write failure.",
            )
        return ApplyResult(
            proposal_id=item.id,
            status="succeeded",
            path=item.path,
            message="Tags written and verified.",
        )

    monkeypatch.setattr(apply_module, "_apply_tag", fake_apply_tag)
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[failed, safe])

    results = apply_module.apply_review_plan(plan, [failed.id, safe.id])

    assert calls == [failed.id, safe.id]
    assert [result.status for result in results] == ["failed", "succeeded"]


def test_apply_continues_after_unrelated_destination_block(tmp_path, monkeypatch):
    safe_source = tmp_path / "safe-source.mp3"
    safe_destination = tmp_path / "safe-destination.mp3"
    blocked_source = tmp_path / "blocked-source.mp3"
    blocked_destination = tmp_path / "blocked-destination.mp3"
    safe_source.write_bytes(b"safe")
    blocked_source.write_bytes(b"blocked")
    blocked_destination.write_bytes(b"existing")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )

    safe = RenameProposal(
        id="rename-safe",
        decision_group_id="safe-group",
        snapshot=FileSnapshot.capture(str(safe_source)),
        old_path=str(safe_source),
        new_path=str(safe_destination),
        current_values={"filename": safe_source.name},
        proposed_values={"filename": safe_destination.name},
        confidence="high",
        reason="test",
    )
    blocked = RenameProposal(
        id="rename-blocked",
        decision_group_id="blocked-group",
        snapshot=FileSnapshot.capture(str(blocked_source)),
        old_path=str(blocked_source),
        new_path=str(blocked_destination),
        current_values={"filename": blocked_source.name},
        proposed_values={"filename": blocked_destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[safe, blocked],
    )

    results = apply_module.apply_review_plan(plan, [blocked.id, safe.id])
    results_by_id = {result.proposal_id: result for result in results}

    assert results_by_id[blocked.id].status == "blocked"
    assert results_by_id[safe.id].status == "succeeded"
    assert not safe_source.exists()
    assert safe_destination.read_bytes() == b"safe"
    assert blocked_source.read_bytes() == b"blocked"
    assert blocked_destination.read_bytes() == b"existing"
    assert apply_module.batches_requiring_recovery() == []


def test_undo_restores_compact_tag_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"post-write")
    state = tmp_path / "state"
    paths = _test_app_paths(state)
    monkeypatch.setattr(apply_module, "ensure_app_dirs", lambda: paths)
    backup = paths["backups"] / "batch" / "tag.metadata.json"
    backup.parent.mkdir(parents=True)
    backup.write_text(
        json.dumps(
            {
                "before": {"artist": "Old Artist", "title": "Old Title"},
                "artwork_before": None,
            }
        ),
        encoding="utf-8",
    )
    written = []
    monkeypatch.setattr(
        apply_module,
        "write_tags_to_file",
        lambda path, values, _artwork=None, **_kwargs: (
            written.append((path, values)) or {"status": "updated"}
        ),
    )
    monkeypatch.setattr(
        apply_module,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Old Artist", "title": "Old Title"},
        ),
    )
    journal = {
        "batch_id": "batch",
        "status": "completed",
        "actions": [
            {
                "kind": "tag",
                "status": "completed",
                "proposal_id": "tag",
                "path": str(source),
                "backup_path": str(backup),
                "post_hash": apply_module.sha256_file(str(source)),
            }
        ],
    }
    (paths["journals"] / "batch.json").write_text(
        json.dumps(journal),
        encoding="utf-8",
    )

    results = apply_module.undo_batch("batch")

    assert results[0].status == "succeeded"
    assert written[0][0] != str(source)
    assert written[0][1] == {
        "artist": "Old Artist",
        "title": "Old Title",
    }


def test_undo_discards_interrupted_tag_temporary_file(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"original")
    temporary = tmp_path / ".songorganizer-batch-tag.mp3"
    temporary.write_bytes(b"temporary")
    state = tmp_path / "state"
    paths = _test_app_paths(state)
    monkeypatch.setattr(apply_module, "ensure_app_dirs", lambda: paths)
    backup = paths["backups"] / "batch" / "tag.metadata.json"
    backup.parent.mkdir(parents=True)
    backup.write_text(json.dumps({"before": {}}), encoding="utf-8")
    (paths["journals"] / "batch.json").write_text(
        json.dumps(
            {
                "batch_id": "batch",
                "status": "applying",
                "actions": [
                    {
                        "kind": "tag",
                        "status": "intent",
                        "proposal_id": "tag",
                        "path": str(source),
                        "backup_path": str(backup),
                        "temporary_path": str(temporary),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    results = apply_module.undo_batch("batch")

    assert results[0].status == "succeeded"
    assert not temporary.exists()
    assert source.read_bytes() == b"original"


def test_undo_restores_original_front_artwork_bytes(tmp_path, monkeypatch):
    fixtures = Path(__file__).parent / "fixtures"
    source = tmp_path / "Artist - Song.mp3"
    shutil.copy2(fixtures / "sample.mp3", source)
    original_cover = fixtures / "cover.jpg"
    replacement_cover = fixtures / "cover.png"
    original_art = {
        "path": str(original_cover),
        "sha256": hashlib.sha256(original_cover.read_bytes()).hexdigest(),
        "size": original_cover.stat().st_size,
        "mime_type": "image/jpeg",
    }
    replacement_art = {
        "path": str(replacement_cover),
        "sha256": hashlib.sha256(replacement_cover.read_bytes()).hexdigest(),
        "size": replacement_cover.stat().st_size,
        "mime_type": "image/png",
        "release_id": "release",
        "source_url": "https://example.invalid/cover.png",
    }
    assert write_tags_to_file(
        str(source),
        {"artist": "Old", "title": "Song"},
        original_art,
    ) == {"status": "updated"}
    before = read_media(str(source))
    proposal = TagProposal(
        id="tag-artwork",
        decision_group_id="song",
        snapshot=FileSnapshot.capture(
            str(source),
            tags=before.tags,
            artwork=before.artwork,
            include_hash=True,
        ),
        path=str(source),
        before=before.tags,
        after={"artist": "New", "title": "Song"},
        confidence="high",
        reason="test",
        artwork_before=before.artwork,
        artwork_after=replacement_art,
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])
    monkeypatch.setattr(
        apply_module,
        "ensure_app_dirs",
        lambda: _test_app_paths(tmp_path / "state"),
    )

    applied = apply_module.apply_review_plan(plan, [proposal.id])
    assert applied[0].status == "succeeded"
    assert read_media(str(source)).artwork.sha256 == replacement_art["sha256"]

    undone = apply_module.undo_batch(plan.batch_id)

    assert undone[0].status == "succeeded"
    assert read_media(str(source)).artwork.sha256 == original_art["sha256"]


def test_undo_removes_artwork_when_original_had_none(tmp_path, monkeypatch):
    fixtures = Path(__file__).parent / "fixtures"
    source = tmp_path / "Artist - Song.mp3"
    shutil.copy2(fixtures / "sample.mp3", source)
    replacement_cover = fixtures / "cover.jpg"
    replacement_art = {
        "path": str(replacement_cover),
        "sha256": hashlib.sha256(
            replacement_cover.read_bytes()
        ).hexdigest(),
        "size": replacement_cover.stat().st_size,
        "mime_type": "image/jpeg",
        "release_id": "release",
        "source_url": "https://example.invalid/cover.jpg",
    }
    assert write_tags_to_file(
        str(source),
        {"artist": "Old", "title": "Song"},
        remove_artwork=True,
    ) == {"status": "updated"}
    before = read_media(str(source))
    assert before.artwork is None
    proposal = TagProposal(
        id="tag-new-artwork",
        decision_group_id="song",
        snapshot=FileSnapshot.capture(
            str(source),
            tags=before.tags,
            artwork=before.artwork,
            include_hash=True,
        ),
        path=str(source),
        before=before.tags,
        after={"artist": "New", "title": "Song"},
        confidence="high",
        reason="test",
        artwork_before=None,
        artwork_after=replacement_art,
    )
    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])
    monkeypatch.setattr(
        apply_module,
        "ensure_app_dirs",
        lambda: _test_app_paths(tmp_path / "state"),
    )

    applied = apply_module.apply_review_plan(plan, [proposal.id])
    assert applied[0].status == "succeeded"
    assert read_media(str(source)).artwork is not None

    undone = apply_module.undo_batch(plan.batch_id)

    assert undone[0].status == "succeeded"
    assert read_media(str(source)).artwork is None


def test_review_plan_round_trips_and_rejects_tampering(tmp_path):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    proposal = RenameProposal(
        id="rename-round-trip",
        decision_group_id="group",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    restored = ReviewPlan.from_dict(plan.to_dict())

    assert restored == plan
    tampered = plan.to_dict()
    tampered["rename_proposals"][0]["reason"] = "changed"
    try:
        ReviewPlan.from_dict(tampered)
    except ValueError:
        pass
    else:
        raise AssertionError("Tampered review plan was accepted")


def test_undo_is_idempotent_after_successful_restore(tmp_path, monkeypatch):
    source = tmp_path / "old.mp3"
    destination = tmp_path / "new.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        apply_module, "ensure_app_dirs", lambda: _test_app_paths(tmp_path / "state")
    )
    proposal = RenameProposal(
        id="rename-idempotent",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source)),
        old_path=str(source),
        new_path=str(destination),
        current_values={"filename": source.name},
        proposed_values={"filename": destination.name},
        confidence="high",
        reason="test",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])

    apply_module.apply_review_plan(plan, [proposal.id])
    first_undo = apply_module.undo_batch(plan.batch_id)
    second_undo = apply_module.undo_batch(plan.batch_id)

    assert first_undo[0].status == "succeeded"
    assert second_undo == []
    assert source.read_bytes() == b"audio"
