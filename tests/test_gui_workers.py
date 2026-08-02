# pylint: disable=import-error

from gui import workers
from gui.workers import BackgroundJobs
from renamer.review_models import ReviewPlan, RenameProposal, canonical_path


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
    jobs.worker.join(timeout=2)

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
    jobs.worker.join(timeout=2)

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
    jobs.worker.join(timeout=2)

    assert observed[0].is_set()
    assert jobs.events.get_nowait()[0] == "organize-complete"
