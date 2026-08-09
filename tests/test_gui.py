# pylint: disable=import-error,protected-access

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from gui import workers
from gui.presentation import format_local_timestamp
from gui.workers import BackgroundJobs
from renamer import apply as apply_module
from renamer.review_models import DuplicateFinding, RenameProposal, ReviewPlan, canonical_path
from renamer.review_service import analyze_folder


def test_history_timestamps_are_converted_to_local_time():
    timestamp = "2026-07-13T07:05:10.468747+00:00"

    expected = datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %I:%M:%S %p %Z")

    assert format_local_timestamp(timestamp) == expected


def test_history_timestamp_falls_back_when_invalid():
    assert format_local_timestamp("not-a-timestamp") == "not-a-timestamp"


def test_organize_worker_only_analyzes_and_never_applies(tmp_path, monkeypatch):
    plan = ReviewPlan.create(str(tmp_path), False)
    calls = {}
    monkeypatch.setattr(
        workers,
        "analyze_folder",
        lambda *_args, **kwargs: calls.update(kwargs) or plan,
    )
    monkeypatch.setattr(
        workers,
        "apply_review_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("organize() must not apply changes automatically")
        ),
    )
    jobs = BackgroundJobs()

    jobs.organize(
        str(tmp_path),
        recursive=False,
        fingerprint=False,
        acoustid_key=None,
        include_artwork=True,
    )
    worker = jobs.worker
    assert worker is not None
    worker.join(timeout=2)

    assert calls["enrich_metadata"]
    assert calls["include_duplicates"]
    assert calls["include_artwork"]
    assert jobs.events.get_nowait() == ("organize-complete", plan, [])


def test_organize_worker_does_not_apply_even_high_confidence_proposals(
    tmp_path,
    monkeypatch,
):
    # A regression guard for the incident where "Organize library" silently
    # renamed hundreds of files, including medium-confidence misidentified
    # ones, with no per-item review. Nothing may leave the analysis phase
    # applied, regardless of confidence.
    from renamer.review_models import FileSnapshot

    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    old_path = canonical_path(str(source))
    new_path = canonical_path(str(tmp_path / "Artist - Renamed.mp3"))
    snapshot = FileSnapshot.capture(old_path)
    proposal = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=old_path,
        new_path=new_path,
        current_values={},
        proposed_values={},
        confidence="high",
        reason="Test proposal.",
    )
    plan = ReviewPlan.create(str(tmp_path), False, rename_proposals=[proposal])
    monkeypatch.setattr(workers, "analyze_folder", lambda *_args, **_kwargs: plan)
    applied = []
    monkeypatch.setattr(
        workers,
        "apply_review_plan",
        lambda *args, **kwargs: applied.append((args, kwargs)),
    )
    jobs = BackgroundJobs()

    jobs.organize(
        str(tmp_path),
        recursive=False,
        fingerprint=False,
        acoustid_key=None,
        include_artwork=True,
    )
    worker = jobs.worker
    assert worker is not None
    worker.join(timeout=2)

    assert applied == []
    event = jobs.events.get_nowait()
    assert event == ("organize-complete", plan, [])


def test_worker_cancel_uses_current_operation_token(tmp_path, monkeypatch):
    observed = []

    def organize(*_args, **kwargs):
        observed.append(kwargs["cancel_event"])
        kwargs["cancel_event"].wait(timeout=2)
        return ReviewPlan.create(str(tmp_path), False)

    monkeypatch.setattr(workers, "analyze_folder", organize)
    jobs = BackgroundJobs()
    jobs.organize(
        str(tmp_path),
        recursive=False,
        fingerprint=False,
        acoustid_key=None,
        include_artwork=True,
    )

    jobs.cancel()
    worker = jobs.worker
    assert worker is not None
    worker.join(timeout=2)

    assert observed[0].is_set()
    assert jobs.events.get_nowait()[0] == "organize-complete"


def test_duplicate_worker_emits_recycle_completion_event(tmp_path, monkeypatch):
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    finding = DuplicateFinding(
        id="duplicate-1",
        paths=(str(first), str(second)),
        classification="auto-safe",
        recommendation="Keep one copy.",
        evidence={},
        confidence="high",
    )
    result = SimpleNamespace(path=str(first), status="succeeded", message="")
    monkeypatch.setattr(workers, "apply_selected_duplicates", lambda *_args: [result])
    jobs = BackgroundJobs()

    jobs.remove_duplicates([(finding, (str(first),))])
    worker = jobs.worker
    assert worker is not None
    worker.join(timeout=2)

    assert jobs.events.get_nowait() == ("duplicate-remove-complete", [result])


def test_gui_services_apply_exact_reviewed_plan_and_undo(
    tmp_path,
    monkeypatch,
    app_paths,
):
    source = tmp_path / "artist - song.mp3"
    source.write_bytes(b"disposable audio fixture")
    monkeypatch.setattr(
        apply_module,
        "ensure_app_dirs",
        lambda: app_paths(tmp_path / "state"),
    )

    plan = analyze_folder(
        str(tmp_path),
        recursive=False,
        include_duplicates=False,
    )

    assert len(plan.rename_proposals) == 1
    proposal = plan.rename_proposals[0]
    results = apply_module.apply_review_plan(plan, [proposal.id])

    assert results[0].status == "succeeded"
    names_after_apply = {path.name for path in tmp_path.iterdir()}
    assert Path(proposal.new_path).name in names_after_apply
    assert source.name not in names_after_apply

    undo_results = apply_module.undo_batch(plan.batch_id)

    assert undo_results[0].status == "succeeded"
    names_after_undo = {path.name for path in tmp_path.iterdir()}
    assert source.name in names_after_undo
    assert Path(proposal.new_path).name not in names_after_undo
