# pylint: disable=import-error

from renamer.planners import analysis
from renamer.planners.analysis import _identity_overrides
from renamer.review_models import (
    DuplicateFinding,
    FileSnapshot,
    RenameProposal,
    TagProposal,
    canonical_path,
    path_key,
)


def test_identity_overrides_prefers_tag_and_rename_proposals_over_nothing(
    tmp_path,
):
    tagged = tmp_path / "tagged.mp3"
    renamed = tmp_path / "renamed.mp3"
    tagged.write_bytes(b"a")
    renamed.write_bytes(b"b")
    tag_snapshot = FileSnapshot.capture(str(tagged))
    rename_snapshot = FileSnapshot.capture(str(renamed))

    tags = [
        TagProposal(
            id="tag-1",
            decision_group_id="group-tag",
            snapshot=tag_snapshot,
            path=canonical_path(str(tagged)),
            before={},
            after={"artist": "Tag Artist", "title": "Tag Title"},
            confidence="high",
            reason="Test tag proposal.",
        )
    ]
    renames = [
        RenameProposal(
            id="rename-1",
            decision_group_id="group-rename",
            snapshot=rename_snapshot,
            old_path=canonical_path(str(renamed)),
            new_path=canonical_path(str(tmp_path / "Rename Artist - Rename Title.mp3")),
            current_values={},
            proposed_values={"artist": "Rename Artist", "title": "Rename Title"},
            confidence="high",
            reason="Test rename proposal.",
        )
    ]

    overrides = _identity_overrides(renames, tags)

    assert overrides[path_key(str(tagged))] == ("Tag Artist", "Tag Title")
    assert overrides[path_key(str(renamed))] == ("Rename Artist", "Rename Title")


def test_identity_overrides_skips_incomplete_identity(tmp_path):
    source = tmp_path / "song.mp3"
    source.write_bytes(b"a")
    snapshot = FileSnapshot.capture(str(source))
    tags = [
        TagProposal(
            id="tag-1",
            decision_group_id="group-tag",
            snapshot=snapshot,
            path=canonical_path(str(source)),
            before={},
            after={"artist": "", "title": "Only Title"},
            confidence="high",
            reason="Test tag proposal.",
        )
    ]

    assert _identity_overrides([], tags) == {}


def test_enrichment_plan_keeps_duplicate_audit_enabled(tmp_path, monkeypatch):
    """Duplicate collection runs concurrently with enrichment, then feeds the
    same collected tracks into grouping once enrichment's identity overrides
    are ready."""
    duplicate = DuplicateFinding(
        id="duplicate",
        paths=("first.mp3", "second.mp3"),
        classification="possible",
        recommendation="Review manually.",
        evidence={},
        confidence="medium",
    )
    calls = {}
    monkeypatch.setattr(
        analysis,
        "plan_metadata_enrichment",
        lambda *_args, **_kwargs: ([], [], []),
    )

    def _fake_collect_tracks(_folder_path, recursive, **kwargs):
        calls["recursive"] = recursive
        calls["fingerprint"] = kwargs.get("fingerprint")
        return ["sentinel-track"]

    monkeypatch.setattr(analysis, "collect_tracks", _fake_collect_tracks)
    monkeypatch.setattr(
        analysis,
        "analyze_regular_duplicates",
        lambda *_args, **_kwargs: [duplicate],
    )

    plan = analysis.analyze_folder(
        str(tmp_path),
        recursive=False,
        fingerprint=True,
        enrich_metadata=True,
    )

    assert plan.duplicate_findings == (duplicate,)
    assert calls["recursive"] is False
    assert calls["fingerprint"] is True
